// SPDX-License-Identifier: MIT
/**
 * Needs someone (redesign §5.1, §4.2 "List card"; screens SCR-01, US-ACT-02): what asks something of a person, worst
 * first, in one list card.
 * - The top three alerts nobody has acknowledged (API-14 rows with `status` open), in API-14's order (by severity then
 *   age), each an `AlertRow` with "Details ›" into Activity and, for operators, Acknowledge with an optional note in an
 *   "Add a note" disclosure. Acknowledge sends one API-15 request with one `Idempotency-Key`; the row then reads
 *   "Acknowledged by jason · 06:47" and stays in place for this visit so that answer can be read.
 * - A mister's watchdog back-off or latch (API-IRR-01 `lines[].fault`), with Clear fault on a latch. A latch whose
 *   `irrigation_line_faulted` alert is in the list carries its Clear fault on that alert's row instead, so it reads once.
 * - "N settings not confirmed ›" (API-02 `writer.differing`), into Settings' Not confirmed filter, unless the stuck
 *   delivery banner already says so.
 * A fence breach's row points to Stop all water in the quick actions rather than repeating it: a page holds one.
 *
 * The open-alert count shows in the head unless the page's answer already counts the alerts (redesign §2.5: the count
 * appears once in the page body). "No open alerts" only after a successful read while the heartbeat and climate are
 * fresh; otherwise "In-app alerts may be missing: …"; a failed read reads "Alerts unavailable".
 */
import { useCallback, useState } from "react";
import { Link } from "react-router";
import { shortTime } from "../../lib/format";
import type { NowAnswer } from "../../lib/now-answer";
import type { Alert, AlertPage, IrrigationLine, IrrigationStatus, Now } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { words } from "../../lib/words";
import { Acknowledge } from "../activity/acknowledge";
import { ALERTS_UNAVAILABLE, AlertRow, NO_OPEN_ALERTS, alertListEmptyText } from "../alert-row";
import { Button } from "../button";
import { ClearFaultButton } from "../clear-fault-button";
import { ObjectRow } from "../layout/object-row";
import { ListCard } from "../layout/list-card";
import type { PanelState } from "../panel";
import { lineName } from "./status";

/** Now lists this many. */
export const TOP_ALERTS = 3;
export const NOT_CONFIRMED_HREF = "/settings?filter=not_confirmed";
const SEVERITY_RANK: Readonly<Record<string, number>> = { critical: 0, warning: 1, info: 2 };
const rank = (severity: string) => SEVERITY_RANK[severity] ?? 3;
const validTime = (value: string | null | undefined): value is string => typeof value === "string" && Number.isFinite(Date.parse(value));

/**
 * The rows Now shows: the unacknowledged ones (`status` open), plus any acknowledged on this page (`keep`), at most
 * three. API-14 lists them by severity, then age (`verdify_services.alerts`: last seen, newest first); Now keeps that
 * order, so its top three are the first unacknowledged rows Activity lists, and only guards the severity order.
 */
export function topAlerts(items: readonly Alert[], keep: ReadonlySet<number> = new Set()): Alert[] {
  return items.filter((alert) => alert.status === "open" || keep.has(alert.id))
    .map((alert, index) => ({ alert, index }))
    .sort((a, b) => rank(a.alert.severity) - rank(b.alert.severity) || a.index - b.index)
    .slice(0, TOP_ALERTS)
    .map(({ alert }) => alert);
}

/** The line an `irrigation_line_faulted` alert names, when it does. */
const faultedLine = (alert: Alert): string | null => {
  const value = alert.alert_type === "irrigation_line_faulted" ? alert.details?.line_id : null;
  return typeof value === "string" ? value : null;
};

/** A mister's back-off or latch in words (D-IRP-19): "South mister: backing off until 13:20", "Center mister: faulted, no misting until midnight (watchdog)". */
export function faultText(line: Pick<IrrigationLine, "line_id" | "name" | "fault" | "fault_until">): string {
  const name = lineName(line);
  if (line.fault === "backing_off") return `${name}: backing off${validTime(line.fault_until) ? ` until ${shortTime(line.fault_until)}` : ""}`;
  if (line.fault === "mister_fault") return `${name}: faulted, no misting until midnight (watchdog)`;
  return `${name}: ${words("line_fault", line.fault ?? "")}`;
}

export type NeedsSomeoneProps = {
  readonly alerts: PanelRead<AlertPage>;
  /** The newest API-02 answer, for the freshness that vouches for an empty list; null before the first. */
  readonly now: Now | null;
  readonly answer: Pick<NowAnswer, "kind">;
  /** The newest API-02 read failed: its freshness is old news, so an empty list cannot vouch for itself. */
  readonly apiFailed: boolean;
  /** API-02 `alerts.open`, the one count; null while unknown. */
  readonly openCount: number | null;
  readonly irrigation: PanelRead<IrrigationStatus>;
};

export function NeedsSomeone({ alerts, now, answer, apiFailed, openCount, irrigation }: NeedsSomeoneProps) {
  // Alerts acknowledged on this page stay in place, so their row can say who acknowledged them and when.
  const [acknowledged, setAcknowledged] = useState<ReadonlySet<number>>(() => new Set());
  const keep = useCallback((alert: Alert) => setAcknowledged((ids) => new Set(ids).add(alert.id)), []);
  const freshness = apiFailed || !now ? null : now.freshness;
  const items = alerts.data ? topAlerts(alerts.data.items, acknowledged) : [];
  const status = irrigation.failed ? null : irrigation.data;
  const latched = new Set(items.map(faultedLine).filter((line): line is string => line !== null));
  const faults = (status?.lines ?? []).filter((line) => line.fault && !latched.has(line.line_id));
  const differing = now && !apiFailed && !now.writer.stuck && now.writer.differing > 0 ? now.writer.differing : 0;
  // An empty list vouches for itself only with API-02's freshness: hold the panel until API-02 first answers.
  const waiting = alerts.pending || (!now && !apiFailed);
  const anything = items.length > 0 || faults.length > 0 || differing > 0;
  const state: PanelState = alerts.failed && !anything ? "error" : waiting || (!alerts.data && !alerts.failed) ? "loading" : anything ? "ready" : "empty";
  const coverage = alertListEmptyText({ readOk: true, ingestor: freshness?.ingestor, climate: freshness?.climate });
  // The count shows once in the page body: here, unless the answer already reads it; and never "· 0" beside the empty line.
  const count = answer.kind === "alerts" || openCount === null || openCount < 1 ? null : openCount;
  return <ListCard id="needs-someone" title="Needs someone" count={count} thing="alerts" state={state} reserve="sm" onRetry={alerts.retry}
    errorText={ALERTS_UNAVAILABLE} link={{ to: "/activity", label: "All alerts" }}
    empty={{ message: coverage, widen: coverage === NO_OPEN_ALERTS ? { to: "/activity?resolved=24h", label: <>Resolved in the last 24 h<span aria-hidden="true"> ›</span></> } : undefined }}>
    {alerts.failed && <p className="fault now-needs-note">{ALERTS_UNAVAILABLE}. <Button onClick={alerts.retry}>Retry</Button></p>}
    {!alerts.failed && coverage !== NO_OPEN_ALERTS && <p className="meta now-needs-note" data-alerts="coverage">{coverage}</p>}
    {items.length > 0 && <ol className="alert-list now-alerts">
      {items.map((alert) => {
        const line = faultedLine(alert);
        const latch = line ? status?.lines.find((row) => row.line_id === line) : undefined;
        const breach = alert.alert_type === "irrigation_fence_breach" && alert.status === "open";
        return <AlertRow key={alert.id} alert={alert} openTo={`/activity?alert=${alert.id}`}
          action={latch && status ? <ClearFaultButton line={latch} engine={status.engine} /> : undefined}
          acknowledge={alert.status === "open" ? <Acknowledge alert={alert} onAcknowledged={keep} /> : undefined}>
          {breach && <p className="meta now-breach-note">Stop all water is in the quick actions above.</p>}
        </AlertRow>;
      })}
    </ol>}
    {faults.length > 0 && <ul className="alert-list now-faults">
      {faults.map((line) => <ObjectRow key={line.line_id} as="li" title={faultText(line)} tone="warn" className="now-fault" data={{ "data-water": "fault" }}
        state={line.fault === "backing_off" ? "The watchdog paused misting on this line; it resumes by itself." : "The watchdog latched this line after repeated faults."}
        stop={status ? <ClearFaultButton line={line} engine={status.engine} /> : undefined} />)}
    </ul>}
    {differing > 0 && <p className="now-unconfirmed"><Link to={NOT_CONFIRMED_HREF}>{differing === 1 ? "1 setting" : `${differing} settings`} not confirmed by the controller<span aria-hidden="true"> ›</span></Link></p>}
  </ListCard>;
}
