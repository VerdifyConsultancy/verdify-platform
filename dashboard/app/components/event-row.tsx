// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { ACTOR_SOURCE_PATTERN, isAiSource, type Actor } from "../lib/vocab";
import { Badge } from "./badge";
import { LocalTime } from "./common";

/**
 * Who wrote, in text (R4): `dashboard` "Jason · dashboard"; `iris` "iris" with AI; `agent:<name>` "<name> · agent"
 * with AI ("steward · agent", `agent:admin` "admin · agent"); `system` "<name> · system" ("forecast-engine · system"),
 * never AI. A source this build does not know shows raw, without AI.
 */
export function attribution(actor: Actor): { text: string; ai: boolean } {
  const { source, name } = actor;
  if (source === "iris") return { text: "iris", ai: true };
  if (source === "dashboard" || source === "system") return { text: `${name} · ${source}`, ai: false };
  if (source.startsWith("agent:") && ACTOR_SOURCE_PATTERN.test(source)) return { text: `${source.slice("agent:".length)} · agent`, ai: isAiSource(source) };
  return { text: `${name} · ${source}`, ai: false };
}

/** The writer in words, with AI for Iris and the agents. */
export function Attribution({ actor }: { actor: Actor }) {
  const { text, ai } = attribution(actor);
  return <span className="event-by">{text}{ai && <> <Badge kind="ai" /></>}</span>;
}

export type EventRowProps = {
  /** When it happened: a mono 24-hour time, America/Denver. */
  at: string;
  /** Adds the date, for a row from another day. */
  showDate?: boolean;
  title: ReactNode;
  badges?: ReactNode;
  actor?: Actor | null;
  /** The muted detail: before → after, manual, target. */
  detail?: ReactNode;
  /** Then the reason: the writer's own words or a `StateReasonLine`. */
  reason?: ReactNode;
  id?: string;
  /** More, such as an Iris plan's changes in a `Details`. */
  children?: ReactNode;
};

/** One row of an activity list (weather's `.event`): mono time, bold title, badges, muted detail, then the reason. */
export function EventRow({ at, showDate = false, title, badges, actor, detail, reason, id, children }: EventRowProps) {
  return <li className="event event-row" id={id}>
    <span className="event-when"><LocalTime at={at} date={showDate} /></span>
    <div className="event-body">
      <h3>{title}{badges}</h3>
      {(actor || detail) && <p>{actor && <Attribution actor={actor} />}{actor && detail && " · "}{detail}</p>}
      {reason && <p className="event-reason">{reason}</p>}
      {children}
    </div>
  </li>;
}
