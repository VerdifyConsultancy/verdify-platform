// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link } from "react-router";
import { duration, formatValue, localDate, shortTime } from "../lib/format";
import { OperatorOnly, useRole } from "../lib/session";
import type { Alert, Freshness } from "../lib/vocab";
import { words } from "../lib/words";
import { LocalTime, cls } from "./common";
import { useNow } from "./freshness";

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
/** The zone, or the line or equipment the alert names in `details`. */
export function alertPlace(alert: Pick<Alert, "zone" | "details">): string | null {
  return alert.zone || detailText(alert.details, "line_id") || detailText(alert.details, "equipment");
}

export type AlertRowProps = {
  alert: Alert;
  /** The plain title; by default the words table's for `alert_type` (raw until #1124 adds the alert types). */
  title?: string;
  /** The unit of `metric_value`, when the screen knows it. */
  unit?: string | null;
  /** The Acknowledge control: rendered only for operators. */
  acknowledge?: ReactNode;
  /** Now's link to the alert in Activity (`/activity?alert=<id>`). */
  openTo?: string;
  id?: string;
  /** More, such as Activity's expanded fields and runbook. */
  children?: ReactNode;
};

/**
 * One open alert, the same on Now and Activity: a 3px rule in its severity colour with the severity word, the plain
 * title, the zone or line and first seen with its age ("since 06:02 (40 min)"); merged repeats read "seen N times"
 * with the last time seen, and the current value shows when the alert has one.
 */
export function AlertRow({ alert, title, unit, acknowledge, openTo, id, children }: AlertRowProps) {
  const now = useNow();
  const role = useRole();
  const severity: string = alert.severity;
  const heading = title ?? words("alert_type", alert.alert_type);
  const today = localDate(new Date(now).toISOString());
  const at = (instant: string) => <LocalTime at={instant} date={localDate(instant) !== today} />;
  const first = Date.parse(alert.first_seen_at);
  const age = Number.isFinite(first) ? duration(Math.max(0, Math.floor((now - first) / 1000))) : null;
  const count = alert.count ?? 1;
  const place = alertPlace(alert);
  const value = alert.metric_value;
  const facts: ReactNode[] = [];
  if (place) facts.push(place);
  facts.push(<>since {at(alert.first_seen_at)}{age && ` (${age})`}</>);
  if (count > 1) facts.push(<>seen {count} times{alert.last_seen_at && <>, last {at(alert.last_seen_at)}</>}</>);
  if (value !== null && value !== undefined && Number.isFinite(value)) facts.push(`value ${formatValue(value, unit)}`);
  const acknowledged = alert.status === "acknowledged";
  const showActions = (acknowledge && role === "operator") || openTo;
  return <li className={cls("event", "event-row", "alert-row", `alert-${SEVERITIES.has(severity) ? severity : "info"}`)} id={id} data-severity={severity}>
    <span className="event-when alert-severity">{words("alert_severity", severity)}</span>
    <div className="event-body">
      <h3>{heading}</h3>
      <p className="alert-facts">{facts.map((fact, index) => <span key={index}>{index > 0 && " · "}{fact}</span>)}</p>
      {alert.message && <p>{alert.message}</p>}
      {acknowledged && <p>Acknowledged{alert.acknowledged_by && ` by ${alert.acknowledged_by}`}{alert.acknowledged_at && <> · {at(alert.acknowledged_at)}</>}</p>}
      {children}
      {showActions && <div className="actions">
        {acknowledge && <OperatorOnly>{acknowledge}</OperatorOnly>}
        {openTo && <Link to={openTo}>Open <span className="screen-reader">{heading}</span><span aria-hidden="true">›</span></Link>}
      </div>}
    </div>
  </li>;
}
