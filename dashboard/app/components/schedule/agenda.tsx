// SPDX-License-Identifier: MIT
import { useId } from "react";
import { shortTime } from "../../lib/format";
import { useApiDown } from "../../lib/session";
import { cls } from "../common";
import { STATUS_WORDS, type AgendaDay, type AgendaRow } from "./model";

function Row({ row, onOpen, selected }: { row: AgendaRow; onOpen?: (row: AgendaRow, opener: HTMLElement) => void; selected: boolean }) {
  const name = `${shortTime(row.at)} ${row.title}${row.detail ? `, ${row.detail}` : ""}${row.status === "planned" ? "" : `, ${STATUS_WORDS[row.status]}`}${row.next ? ", next" : ""}`;
  const body = <>
    <time className="sch-agenda-time" dateTime={row.at}>{shortTime(row.at)}</time>
    <span className="sch-agenda-what">
      <span className="sch-agenda-title">{row.title}</span>
      {row.detail && <span className="sch-agenda-detail">{row.detail}</span>}
    </span>
    {row.next && <span className="tag sch-next">Next</span>}
  </>;
  const className = cls("sch-agenda-row", `sch-${row.status}`, row.entry.schedule?.degraded_only && "sch-eq-heat1", selected && "is-selected");
  return <li data-status={row.status} data-line={row.lineId}>
    {onOpen
      ? <button type="button" className={className} aria-label={name} aria-haspopup="dialog" data-block={row.blockKey} onClick={(event) => onOpen(row, event.currentTarget)}>{body}</button>
      : <div className={className}>{body}</div>}
  </li>;
}

export type AgendaProps = {
  days: readonly AgendaDay[];
  /** "L07 South mister: paused until Sat 09:00 by jason", above the days. */
  pauses?: readonly string[];
  /** The open popover's occurrence. */
  selected?: string | null;
  onOpen?: (row: AgendaRow, opener: HTMLElement) => void;
};

/**
 * The agenda (redesign §6.1, §6.7), the phones' default and, on desktop, the timeline's equivalent for the keyboard and
 * screen readers: from local midnight of the selected day, three days, by day. Each run reads its time, line and amount
 * and its label with what happened or why it will not run (hatched); the next planned run carries NEXT. Today's rows
 * before now fold into "Earlier today" once it is past noon. A row opens its popover (a sheet on phones).
 */
export function Agenda({ days, pauses = [], selected = null, onOpen }: AgendaProps) {
  const id = useId();
  const apiDown = useApiDown();
  return <div className={cls("sch-agenda", apiDown && "is-neutral")}>
    {pauses.length > 0 && <ul className="sch-agenda-pauses">{pauses.map((pause) => <li key={pause}>{pause}</li>)}</ul>}
    {days.map((day) => <section key={day.date} className="sch-agenda-day" aria-labelledby={`${id}-${day.date}`}>
      <h3 id={`${id}-${day.date}`} className="sch-agenda-head">{day.title}</h3>
      {day.earlier.length > 0 && <details className="sch-earlier">
        <summary>Earlier today ({day.earlier.length})</summary>
        <ul className="sch-agenda-rows">{day.earlier.map((row) => <Row key={row.key} row={row} onOpen={onOpen} selected={selected === row.blockKey} />)}</ul>
      </details>}
      {day.rows.length > 0
        ? <ul className="sch-agenda-rows">{day.rows.map((row) => <Row key={row.key} row={row} onOpen={onOpen} selected={selected === row.blockKey} />)}</ul>
        : day.earlier.length === 0 && <p className="meta sch-agenda-empty">No runs</p>}
    </section>)}
  </div>;
}
