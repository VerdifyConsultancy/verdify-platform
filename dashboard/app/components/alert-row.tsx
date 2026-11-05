// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link } from "react-router";
import { duration, localDate, shortTime } from "../lib/format";
import { OperatorOnly } from "../lib/session";
import type { Alert, Freshness } from "../lib/vocab";
import { hasWords, words } from "../lib/words";
import { LocalTime, cls } from "./common";
import { useNow } from "./freshness";
import { ObjectRow } from "./layout/object-row";

/** The heartbeat is fresh at most this old, and the controller reports while its newest climate row is at most this old (architecture §7). */
export const HEARTBEAT_FRESH_S = 180;
export const CLIMATE_REPORTING_S = 300;
export const NO_OPEN_ALERTS = "No open alerts";
export const ALERTS_UNAVAILABLE = "Alerts unavailable";
const MAY_BE_MISSING = "In-app alerts may be missing";

const sinceText = (observedAt: string | null) => (observedAt && Number.isFinite(Date.parse(observedAt)) ? ` since ${shortTime(observedAt)}` : "");

/**
 * What an empty alert list says (screens §1.5). "No open alerts" only after a successful read while the ingestor
 * heartbeat is at most 180 s old and the newest climate row at most 300 s old (ages as API-02 `freshness` gives them).
 * A stale heartbeat reads "In-app alerts may be missing: ingestor not running since HH:MM"; a controller not reporting,
 * "In-app alerts may be missing: no data since HH:MM"; a failed read, "Alerts unavailable". Without API-02's freshness
 * (it failed or has not answered) the list cannot vouch for itself either: hold the panel in its loading state until
 * API-02 first answers, and after that a missing freshness reads "In-app alerts may be missing: can't reach the
 * greenhouse data".
 */
export function alertListEmptyText({ readOk, ingestor, climate }: { readOk: boolean; ingestor: Freshness | null | undefined; climate: Freshness | null | undefined }): string {
  if (!readOk) return ALERTS_UNAVAILABLE;
  if (!ingestor || !climate) return `${MAY_BE_MISSING}: can't reach the greenhouse data`;
  const heartbeatFresh = !ingestor.stale && ingestor.age_s !== null && ingestor.age_s <= HEARTBEAT_FRESH_S;
  if (!heartbeatFresh) return `${MAY_BE_MISSING}: ingestor not running${sinceText(ingestor.observed_at)}`;
  const reporting = climate.age_s !== null && climate.age_s <= CLIMATE_REPORTING_S;
  if (!reporting) return `${MAY_BE_MISSING}: no data${sinceText(climate.observed_at)}`;
  return NO_OPEN_ALERTS;
}

const SEVERITIES = new Set(["critical", "warning", "info"]);
function detailText(details: unknown, key: string): string | null {
  if (!details || typeof details !== "object") return null;
  const value = (details as Record<string, unknown>)[key];
  return typeof value === "string" && value ? value : null;
}
/** The zone, or the line or equipment the alert names in `details`, as the API gives it ("south", "L02", "fan2"). */
export function alertPlace(alert: Pick<Alert, "zone" | "details">): string | null {
  return alert.zone || detailText(alert.details, "line_id") || detailText(alert.details, "equipment");
}
/** The place in words: equipment by its plain name ("Fan 2"), a zone capitalised ("South"), a line as its id. */
export function alertPlaceWords(alert: Pick<Alert, "zone" | "details">): string | null {
  const place = alertPlace(alert);
  if (!place) return null;
  if (!alert.zone && !detailText(alert.details, "line_id") && hasWords("equipment", place)) return words("equipment", place);
  return alert.zone === place ? place.charAt(0).toUpperCase() + place.slice(1) : place;
}

/**
 * When an alert started and how often it came back (redesign §5.8): "since 06:02 (40 min)" for one, "×37 since Mon 02:10
 * · last 06:02" for merged repeats. A time on another day carries its date.
 */
export function alertWhen(alert: Pick<Alert, "first_seen_at" | "last_seen_at" | "count">, now: number): ReactNode {
  const today = localDate(new Date(now).toISOString());
  const at = (instant: string) => <LocalTime at={instant} date={localDate(instant) !== today} />;
  const count = alert.count ?? 1;
  if (count > 1) return <>×{count} since {at(alert.first_seen_at)}{alert.last_seen_at && <> · last {at(alert.last_seen_at)}</>}</>;
  const first = Date.parse(alert.first_seen_at);
  const age = Number.isFinite(first) ? duration(Math.max(0, Math.floor((now - first) / 1000))) : null;
  return <>since {at(alert.first_seen_at)}{age && ` (${age})`}</>;
}

export type AlertRowProps = {
  alert: Alert;
  /** The plain title; by default the words table's for `alert_type` (raw for a type it lacks). */
  title?: string;
  /** The unit of `metric_value`, when the screen knows it (the expanded fields show the value). */
  unit?: string | null;
  /** The Acknowledge control (with its optional note): rendered only for operators, under the row's facts. */
  acknowledge?: ReactNode;
  /** Now's link to the alert in Activity (`/activity?alert=<id>`), read "Details ›". */
  openTo?: string;
  /** Activity's own link that expands the row in place, instead of `openTo`. */
  details?: ReactNode;
  /** What the alert calls for at once: Stop all water on a fence breach, Clear fault on a latched mister. */
  action?: ReactNode;
  /** Hides the message line: a screen that says the same live (the flow alert's lines) passes false. */
  message?: boolean;
  id?: string;
  /** More, such as Activity's expanded fields and runbook. */
  children?: ReactNode;
};

/**
 * One alert, the same on Now and Activity (redesign §5.8), as an `ObjectRow` in a list: a 3 px rule in its severity
 * colour (fault for critical, amber for a warning, muted for info) with the severity word, the plain title, the place
 * and when it started, merged repeats as "×3 since 06:02 · last 06:40", the ingestor's message, who acknowledged it, and
 * its actions: what it calls for (Stop, Clear fault), "Details ›" and, for operators, Acknowledge with an optional note.
 */
export function AlertRow({ alert, title, acknowledge, openTo, details, action, message = true, id, children }: AlertRowProps) {
  const now = useNow();
  const severity: string = alert.severity;
  const known = SEVERITIES.has(severity) ? severity : "info";
  const heading = title ?? words("alert_type", alert.alert_type);
  const today = localDate(new Date(now).toISOString());
  const place = alertPlaceWords(alert);
  const acknowledged = alert.status === "acknowledged";
  const link = details ?? (openTo ? <Link className="alert-open" to={openTo}>Details <span className="screen-reader">of {heading} </span><span aria-hidden="true">›</span></Link> : null);
  const facts = <>{place && <>{place} · </>}{alertWhen(alert, now)}</>;
  return <ObjectRow as="li" id={id} title={heading} className={cls("alert-row", `alert-${known}`)} tone={known === "critical" ? "fault" : known === "warning" ? "warn" : undefined}
    tag={<span className="alert-severity">{words("alert_severity", severity)}</span>} data={{ "data-severity": severity }}
    state={<span className="alert-facts">{facts}</span>}
    meta={acknowledged || (message && alert.message) ? <>
      {message && alert.message && <span className="alert-message">{alert.message}</span>}
      {acknowledged && <span className="alert-acked">Acknowledged{alert.acknowledged_by && ` by ${alert.acknowledged_by}`}{alert.acknowledged_at && <> · <LocalTime at={alert.acknowledged_at} date={localDate(alert.acknowledged_at) !== today} /></>}</span>}
    </> : undefined}
    stop={action} menu={link ?? undefined}>
    {acknowledge && <OperatorOnly><div className="alert-ack">{acknowledge}</div></OperatorOnly>}
    {children}
  </ObjectRow>;
}
