// SPDX-License-Identifier: MIT
import { actorWords, entryLine, planChangeText, type Catalog, type SignedIn } from "../lib/activity";
import { localDate } from "../lib/format";
import type { ActivityEntry } from "../lib/operator-api";
import { Badge, ReadbackBadge } from "./badge";
import { LocalTime } from "./common";

export type EventRowProps = {
  entry: ActivityEntry;
  /** API-09's settings by param, to name a setting and print its values. */
  catalog: Catalog;
  /** The signed-in person, who reads by name. */
  me: SignedIn;
  id?: string;
};

/**
 * One log entry in one line (redesign §5.9): the time (mono, America/Denver, HH:MM: the entry sits under its day's
 * header, on the log and in Recent activity alike), who wrote it (AI for Iris and the agents only), what changed ("VPD
 * Mister Engage 1.60 → 1.55 kPa · manual"), the writer's reason in quotes, and the readback outcome as a pill with its
 * time. An Iris plan of several changes reads "SUNRISE plan · 9 changes" and expands to them. An automatic entry leads
 * with its cause ("Forecast rule “hot afternoon” · …").
 */
export function EventRow({ entry, catalog, me, id }: EventRowProps) {
  const line = entryLine(entry, catalog);
  const actor = actorWords(entry.actor, me);
  const day = localDate(entry.ts);
  return <li className="event act-entry" id={id}>
    <span className="event-when"><LocalTime at={entry.ts} /></span>
    <span className="event-by">{actor.text}{actor.ai && <> <Badge kind="ai" /></>}</span>
    <span className="act-entry-what">
      {line.changes.length > 1
        ? <details className="act-plan"><summary>{line.text}</summary>
          <ul className="plan-changes">{line.changes.map((change, index) => <li key={index}>{planChangeText(change, catalog, day)}</li>)}</ul>
        </details>
        : <span className="act-entry-text">{line.text}</span>}
      {entry.reason && <span className="event-reason">“{entry.reason}”</span>}
    </span>
    <span className="act-entry-outcome">
      {entry.state && <ReadbackBadge state={entry.state} />}
      {entry.state && entry.confirmed_at && <> <span className="meta"><LocalTime at={entry.confirmed_at} date={localDate(entry.confirmed_at) !== day} /></span></>}
    </span>
  </li>;
}
