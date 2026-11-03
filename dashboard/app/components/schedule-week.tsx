// SPDX-License-Identifier: MIT
import { Fragment, useId, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { localDate } from "../lib/format";
import { lineById } from "../lib/lines";
import type { IrrigationLine, IrrigationStatus, Occurrence, Schedule, ScheduleAgenda } from "../lib/operator-api";
import { PAST_CAP_TEXT, replacedByDraft, type ScheduleDraft, type SchedulePreview } from "../lib/schedule-preview";
import { RETRYING_TEXT, SCHEDULE_LINES, blockAmount, feedStages, occurrenceWords, weekDays } from "../lib/water";
import { programSyncText } from "../lib/writes";
import { Badge, ReadbackBadge } from "./badge";
import { Chips } from "./chips";
import { LocalTime, cls } from "./common";
import { EmptyState } from "./panel";

/** The empty week (screens SCR-04). */
export const NO_SCHEDULES_TEXT = "No schedules yet. Lines water only from VPD control and Run now.";
const DEGRADED_TEXT = "only if VPD sensing is degraded";

const WEEKDAY = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short" });
/** A column's day: "Mon 11/2". */
export function weekDayLabel(day: string): string {
  const [, month, date] = day.split("-").map(Number);
  return `${WEEKDAY.format(new Date(`${day}T12:00:00Z`))} ${month}/${date}`;
}

/** What selecting a block offers: the pause (#1182) and Edit (#1137); null or nothing for a block that offers none. */
export type BlockMenu = (occurrence: Occurrence, schedule: Schedule | undefined) => ReactNode;

/** A row of a cell: a saved occurrence, or one of the editor's outlines. */
type Entry = {
  readonly occurrence: Occurrence;
  readonly schedule: Schedule | ScheduleDraft | undefined;
  /** A draft's outline, drawn PLANNED (#1137). */
  readonly draft: boolean;
  readonly pastCap: boolean;
  /** A saved block the open editor's draft replaces: out of view until it closes, so its Edit can take focus back. */
  readonly replaced: boolean;
};
type BlockProps = Entry & {
  kind: string | undefined;
  now: number;
  program: Pick<IrrigationStatus, "engine" | "program"> | null | undefined;
  /** The schedule just saved here: its blocks keep their badge through CONFIRMED. */
  saved: number | null | undefined;
  /** What selecting this block offers; without it the block is not selectable. */
  menu: ReactNode;
  open: boolean;
  onToggle: () => void;
};

/**
 * A block: start time, amount, FERT, MANUAL or "Iris may adjust", then its label, a feed's stages and its notes; while a
 * write settles, the schedule's sync with its words ("REQUESTED rev 43 sending"). Selecting one that has a menu opens it,
 * and Escape closes it and puts focus back on the block.
 */
function Block({ occurrence, schedule, draft, pastCap, replaced, kind, now, program, saved, menu, open, onToggle }: BlockProps) {
  const select = useRef<HTMLElement>(null);
  const fert = kind === "fert_drip" || kind === "fert_mister";
  const hatched = occurrence.status === "skipped" || occurrence.status === "blocked";
  const past = Date.parse(occurrence.planned_start) < now;
  const sync = schedule && "sync" in schedule ? schedule.sync : null;
  const syncWords = sync && sync !== "confirmed" ? programSyncText(sync, program) : null;
  const showSync = Boolean(sync) && (sync !== "confirmed" || (schedule && schedule.slot_id === saved));
  // Before the engine a schedule's words and the API's blocked note say one thing.
  const note = occurrence.status === "blocked" && syncWords ? null : occurrenceWords(occurrence, now);
  // The fallback slots say when they run even where the API's note does not; one that will not run says only why.
  const degraded = schedule?.degraded_only && occurrence.status !== "skipped" && !(note ?? "").includes("VPD sensing is degraded") ? DEGRADED_TEXT : null;
  const stages = schedule ? feedStages(schedule, kind) : null;
  const body = <>
    <span className="week-block-head">
      <LocalTime at={occurrence.planned_start} />
      {schedule && <> <span className="week-block-amount">{blockAmount(schedule, kind)}</span></>}
      {fert && <> <Badge kind="fert" /></>}
      {schedule && <> {schedule.manual ? <Badge kind="manual" /> : <span className="week-block-iris">Iris may adjust</span>}</>}
      {draft && <> <Badge kind="planned" /></>}
      {showSync && sync && <> <ReadbackBadge state={sync} /></>}
    </span>
    {schedule?.label && <span className="week-block-label">{schedule.label}</span>}
    {stages && <span className="week-block-detail">{stages}</span>}
    {degraded && <span className="week-block-note">{degraded}</span>}
    {note && <span className="week-block-note">{note}</span>}
    {syncWords && <span className="week-block-note">{syncWords}{sync === "not_confirmed" && ` · ${RETRYING_TEXT}`}</span>}
    {pastCap && <span className="week-block-note">{PAST_CAP_TEXT}</span>}
  </>;
  return <li className={cls("week-block", hatched && "week-block-hatched", past && "week-block-past", draft && "week-block-draft")} data-status={occurrence.status} data-slot={occurrence.slot_id}
    hidden={replaced || undefined} onKeyDown={(event) => {
      if (!open || event.key !== "Escape") return;
      onToggle();
      select.current?.focus();
    }}>
    {menu
      // The state decides, not the browser's own toggle, so one menu is open at a time.
      ? <details className="week-block-menu" open={open}>
        <summary ref={select} onClick={(event) => {
          event.preventDefault();
          onToggle();
        }}>{body}</summary>
        <div className="week-block-actions">{menu}</div>
      </details>
      : body}
  </li>;
}

export type ScheduleWeekProps = {
  agenda: ScheduleAgenda;
  /** API-IRR-01's lines, for names, kinds and reserved; the register's line ids alone without them. */
  lines?: readonly IrrigationLine[];
  /** API-IRR-01's `engine` and `program`, for the words beside a schedule's sync badge. */
  program?: Pick<IrrigationStatus, "engine" | "program"> | null;
  /** "Last 7 days" (`past_days=7`): the seven days before today, each past block with its outcome word. */
  past: boolean;
  /** Today's America/Denver date. */
  today: string;
  /** Now (epoch ms), to the minute: which blocks are past. */
  now: number;
  /** The Next 7 days and Last 7 days chips, from the route. */
  views?: ReactNode;
  /** "+ New schedule" (#1137). */
  actions?: ReactNode;
  /** The open editor's draft, drawn as PLANNED outlines (#1137): it replaces the saved schedule's blocks still ahead. */
  draft?: SchedulePreview | null;
  /** The URL that edits a schedule (`?schedule=<slot_id>`), for operators: a disabled schedule has no block, so the line under the week links here. */
  editTo?: (slotId: number) => string;
  /** The schedule just saved here, whose blocks read REQUESTED and then stay CONFIRMED. */
  saved?: number | null;
  /** What selecting a block offers; without it a block is not selectable. */
  blockMenu?: BlockMenu;
};

const startOf = (entry: Entry) => Date.parse(entry.occurrence.planned_start);

/**
 * ScheduleWeek (design system §4.6): rows L01, L02 and L04–L10 (L05 and L06 marked reserved while disabled), columns
 * today and the next six days, or the seven before today. Each API-IRR-02 occurrence sits in its line × local-day cell,
 * joined to its schedule by `slot_id`: start time, "12m" or "8×60s", FERT on a fertilizer line, MANUAL or "Iris may
 * adjust"; one that will not run is hatched with why, and a past one carries its outcome word. Selecting a block that
 * has a `blockMenu` opens it, one at a time. The editor's draft previews as PLANNED outlines. A disabled schedule has no
 * occurrences, so the line under the week reaches it. At phone width a day switcher shows one day for every line.
 */
export function ScheduleWeek({ agenda, lines = [], program, past, today, now, views, actions, draft, editTo, saved, blockMenu }: ScheduleWeekProps) {
  const headingId = useId();
  const days = weekDays(agenda, today, past);
  const [chosen, setChosen] = useState<string | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const selected = chosen !== null && days.includes(chosen) ? chosen : past ? days[days.length - 1] : days[0];
  const byLine = new Map(lines.map((line) => [line.line_id, line]));
  const schedules = new Map(agenda.schedules.map((schedule) => [schedule.slot_id, schedule]));
  const entries: Entry[] = [
    ...agenda.occurrences.map((occurrence) => ({
      occurrence, schedule: schedules.get(occurrence.slot_id), draft: false, pastCap: false, replaced: draft ? replacedByDraft(occurrence, draft.slotId, now) : false,
    })),
    ...(draft?.blocks ?? []).map((block) => ({ occurrence: block.occurrence, schedule: block.schedule, draft: true, pastCap: block.pastCap, replaced: false })),
  ];
  const cells = new Map<string, Entry[]>();
  for (const entry of entries.sort((a, b) => startOf(a) - startOf(b))) {
    const key = `${entry.occurrence.line_id}|${localDate(entry.occurrence.planned_start)}`;
    cells.set(key, [...(cells.get(key) ?? []), entry]);
  }
  const disabled = agenda.schedules.filter((schedule) => !schedule.enabled);
  const dayClass = (day: string) => cls(day === selected && "is-day", day === today && "week-today");
  return <section className="schedule-week" aria-labelledby={headingId}>
    <div className="section-head week-head">
      <h2 id={headingId} className="eyebrow">Week · {past ? "last 7 days" : "planned"}</h2>
      <p className="week-count">{agenda.counts.schedules} of {agenda.counts.max_schedules} schedules</p>
      {views}
      {actions}
    </div>
    {agenda.counts.schedules === 0 && agenda.schedules.length === 0 && !draft?.blocks.length ? <EmptyState message={NO_SCHEDULES_TEXT} /> : <>
      <div className="week-days">
        <Chips label="Day" small items={days.map((day) => ({ key: day, label: weekDayLabel(day), pressed: day === selected, onToggle: () => setChosen(day) }))} />
      </div>
      <div className="table-wrap week-wrap" role="region" tabIndex={0} aria-label="Schedule week">
        <table className="week-table">
          <caption className="screen-reader">Schedule occurrences by line and day, America/Denver</caption>
          <thead><tr>
            <th scope="col">Line</th>
            {days.map((day) => <th key={day} scope="col" data-day={day} className={dayClass(day)}>{weekDayLabel(day)}{day === today && <span className="week-today-word"> today</span>}</th>)}
          </tr></thead>
          <tbody>{SCHEDULE_LINES.map((id) => {
            const line = byLine.get(id);
            const kind = line?.kind ?? lineById(id)?.kind;
            // Reserved while still disabled, as the line cards say (Round 2b E); the register's L05 and L06 without the lines.
            const reserved = line ? line.reserved && !line.enabled : id === "L05" || id === "L06";
            return <tr key={id} data-line={id}>
              <th scope="row">{id}{line && <> {line.name}</>}{reserved && <> <span className="tag tag-muted">Reserved</span></>}</th>
              {days.map((day) => {
                const blocks = cells.get(`${id}|${day}`) ?? [];
                return <td key={day} data-day={day} className={dayClass(day)}>
                  {blocks.length > 0 && <ul className="week-blocks">{blocks.map((entry) => {
                    const key = `${entry.draft ? "draft" : entry.occurrence.slot_id}-${entry.occurrence.planned_start}`;
                    // A draft's outline is the form's: it has no menu.
                    const offered = entry.draft ? null : blockMenu?.(entry.occurrence, schedules.get(entry.occurrence.slot_id));
                    return <Block key={key} {...entry} kind={kind} now={now} program={program} saved={saved} menu={offered} open={menu === key} onToggle={() => setMenu((current) => (current === key ? null : key))} />;
                  })}</ul>}
                  {blocks.every((entry) => entry.replaced) && <span className="week-none" aria-hidden="true">·</span>}
                </td>;
              })}
            </tr>;
          })}</tbody>
        </table>
      </div>
      <p className="week-key small">MANUAL: Iris skips it · Iris may adjust: Iris can change it · hatched: will not run, with why · FERT: prewet and flush on the clean line, feed through L03{draft && " · PLANNED outline: the draft you are editing"}{blockMenu && " · select a block to edit its schedule or, while it is planned, pause its line until after it"}</p>
    </>}
    {disabled.length > 0 && <p className="week-disabled small">Disabled, so not in the week: {disabled.map((schedule, index) => {
      const name = `${schedule.line_id} ${schedule.label || `slot ${schedule.slot_id}`}`;
      return <Fragment key={schedule.slot_id}>{index > 0 && " · "}{editTo ? <Link to={editTo(schedule.slot_id)} preventScrollReset>{name}</Link> : name}</Fragment>;
    })}</p>}
  </section>;
}
