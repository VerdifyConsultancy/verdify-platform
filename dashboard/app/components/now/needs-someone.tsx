// SPDX-License-Identifier: MIT
/**
 * Needs someone (screens SCR-01, US-ACT-02): the top three alerts nobody has acknowledged (API-14 rows with `status`
 * open), by severity then age, each in #1123's `AlertRow` with Open and, for operators, Acknowledge with an optional
 * note in an "Add a note" disclosure. Acknowledge sends one API-15 request with one `Idempotency-Key` (#1125's R hook)
 * and the row then reads "Acknowledged by jason · 06:47"; it stays in place for this visit so that answer can be read.
 * The head carries the one open-alert count (API-02 `alerts.open`), never a count of its own.
 *
 * "No open alerts" only after a successful read while the heartbeat and climate are fresh; otherwise "In-app alerts
 * may be missing: …" (#1123's `alertListEmptyText`); a failed read reads "Alerts unavailable".
 */
import { useCallback, useState } from "react";
import { Link } from "react-router";
import type { Alert, AlertPage, Now } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { useRecordWrite } from "../../lib/writes";
import { words } from "../../lib/words";
import { ALERTS_UNAVAILABLE, AlertRow, NO_OPEN_ALERTS, alertListEmptyText } from "../alert-row";
import { Button } from "../button";
import { Details } from "../details";
import { Reason } from "../forms/reason";
import { EmptyState, Panel, type PanelState } from "../panel";

/** Now lists this many. */
export const TOP_ALERTS = 3;
const SEVERITY_RANK: Readonly<Record<string, number>> = { critical: 0, warning: 1, info: 2 };
const rank = (severity: string) => SEVERITY_RANK[severity] ?? 3;
const seen = (alert: Pick<Alert, "first_seen_at">) => {
  const at = Date.parse(alert.first_seen_at);
  return Number.isFinite(at) ? at : Number.POSITIVE_INFINITY;
};

/**
 * The rows Now shows: the unacknowledged ones (`status` open), plus any acknowledged on this page (`keep`), by severity
 * (critical, warning, info) then age, oldest first, at most three.
 */
export function topAlerts(items: readonly Alert[], keep: ReadonlySet<number> = new Set()): Alert[] {
  return items.filter((alert) => alert.status === "open" || keep.has(alert.id))
    .map((alert, index) => ({ alert, index }))
    .sort((a, b) => rank(a.alert.severity) - rank(b.alert.severity) || seen(a.alert) - seen(b.alert) || a.index - b.index)
    .slice(0, TOP_ALERTS)
    .map(({ alert }) => alert);
}

/** Acknowledge, with the optional note (at most 500 characters) in its disclosure. */
function Acknowledge({ alert, onAcknowledged }: { alert: Alert; onAcknowledged: (alert: Alert) => void }) {
  const record = useRecordWrite();
  const [note, setNote] = useState("");
  const title = words("alert_type", alert.alert_type);
  const send = async () => {
    const outcome = await record.acknowledge(alert.id, note.trim() || null);
    if (outcome.ok) onAcknowledged(outcome.data);
  };
  return <div className="ack">
    <Details summary="Add a note" className="ack-note">
      <Reason value={note} onChange={setNote} label="Note (optional)" error={record.error?.field === "note" ? record.error.message : null} />
    </Details>
    <Button write wide busy={record.sending} onClick={() => void send()}>Acknowledge<span className="screen-reader"> {title}</span></Button>
    {record.error && record.error.field !== "note" && <p className="field-error">{record.error.message}</p>}
  </div>;
}

export type NeedsSomeoneProps = {
  readonly alerts: PanelRead<AlertPage>;
  /** The newest API-02 answer, for the freshness that vouches for an empty list; null before the first. */
  readonly now: Now | null;
  /** The newest API-02 read failed: its freshness is old news, so an empty list cannot vouch for itself. */
  readonly apiFailed: boolean;
  /** API-02 `alerts.open`, the one count; null while unknown. */
  readonly openCount: number | null;
};

export function NeedsSomeone({ alerts, now, apiFailed, openCount }: NeedsSomeoneProps) {
  // Alerts acknowledged on this page stay in place, so their row can say who acknowledged them and when.
  const [acknowledged, setAcknowledged] = useState<ReadonlySet<number>>(() => new Set());
  const keep = useCallback((alert: Alert) => setAcknowledged((ids) => new Set(ids).add(alert.id)), []);
  const freshness = apiFailed || !now ? null : now.freshness;
  const items = alerts.data ? topAlerts(alerts.data.items, acknowledged) : [];
  // An empty list vouches for itself only with API-02's freshness: hold the panel until API-02 first answers.
  const waiting = alerts.pending || (!now && !apiFailed);
  const state: PanelState = alerts.failed ? "error" : waiting || !alerts.data ? "loading" : items.length ? "ready" : "empty";
  const coverage = alertListEmptyText({ readOk: true, ingestor: freshness?.ingestor, climate: freshness?.climate });
  const title = `Needs someone${openCount !== null ? ` · ${openCount}` : ""}`;
  return <Panel id="needs-someone" title={title} thing="alerts" state={state} reserve="sm" onRetry={alerts.retry} errorText={ALERTS_UNAVAILABLE}
    actions={<Link className="card-link-more" to="/activity">All alerts<span aria-hidden="true"> ›</span></Link>}
    empty={<EmptyState message={coverage} />}>
    {coverage !== NO_OPEN_ALERTS && <p className="state-note" data-alerts="coverage">{coverage}</p>}
    <ul className="events now-alerts">
      {items.map((alert) => <AlertRow key={alert.id} alert={alert} openTo={`/activity?alert=${alert.id}`}
        acknowledge={alert.status === "open" ? <Acknowledge alert={alert} onAcknowledged={keep} /> : undefined} />)}
    </ul>
  </Panel>;
}
