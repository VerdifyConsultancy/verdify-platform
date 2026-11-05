// SPDX-License-Identifier: MIT
import { useMemo, useState, type ReactNode } from "react";
import { Link, replace, useSearchParams } from "react-router";
import { Acknowledge } from "../components/activity/acknowledge";
import { ALERTS_UNAVAILABLE, AlertRow, alertListEmptyText } from "../components/alert-row";
import { Button } from "../components/button";
import { LocalTime } from "../components/common";
import { Details } from "../components/details";
import { EventRow } from "../components/event-row";
import { FieldHint, useFieldIds } from "../components/forms";
import { AgeLabel, useClockValue } from "../components/freshness";
import { Kv, type KvItem } from "../components/kv";
import { ListCard } from "../components/layout/list-card";
import { PageHeader } from "../components/layout/page-header";
import type { PanelState } from "../components/panel";
import { StopAllWater } from "../components/stop-all-water";
import { useToast } from "../components/toast";
import {
  activityHref, alertIdOf, alertsStatus, catalogOf, detailText, groupAlerts, isClosed, logRedirect, personName, readView, type ActivityView, type SignedIn,
} from "../lib/activity";
import { formatNumber, formatValue, localDate, shortTime } from "../lib/format";
import { graphWindowUrl } from "../lib/graph-link";
import { lineByEquipment } from "../lib/lines";
import type { Alert, AlertPage, IrrigationStatus, Now } from "../lib/operator-api";
import { panelRead, useApiRead, type PanelRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";
import { OperatorOnly, useWritesEnabled } from "../lib/session";
import { useMe, useShellNow, useShellStatus } from "../lib/shell";
import { words } from "../lib/words";
import { REASON_MAX, useCommand, useRecordWrite } from "../lib/writes";

export const meta = () => viewMeta("activity-alerts");

/**
 * An old log link (`/activity?target=L01`, D-UX-03) lands on Activity → Log with the same query, replacing the history
 * entry: from the server for a page load, in the browser for an in-app link. `?alert=7` and `?resolved=24h` stay here.
 */
export function loader({ request }: { request: Request }) {
  const to = logRedirect(new URL(request.url));
  return to ? replace(to) : null;
}
// Its own function, never `= loader`, using only imported names: the client build strips `loader` and splits
// `clientLoader` into a chunk of its own, so naming `loader` here threw as the page's module loaded and the page never
// hydrated (`routes.test.ts`).
export function clientLoader({ request }: { request: Request }) {
  const to = logRedirect(new URL(request.url));
  return to ? replace(to) : null;
}

/** Alerts every 15 s; the water status and the recent activity every 60 s (screens §7). */
const ALERTS_POLL_MS = 15_000;
const SLOW_POLL_MS = 60_000;
/** API-14's largest page: the open alerts and those resolved in the last 24 h fit in one. */
const ALERT_LIMIT = 200;
const RECENT_ALERTS = { status: "recent", limit: ALERT_LIMIT } as const;
/** Recent activity: the five newest log entries. */
const RECENT_ACTIVITY = { limit: 5 } as const;
/** "Open in Graphs" shows 2 h either side of an alert's first seen. */
const GRAPH_MARGIN_MS = 2 * 3_600_000;

/**
 * Activity → Alerts (`/activity`, redesign §5.8; SCR-06): what needs someone now. One API-14 read (`status=recent`, every
 * 15 s) gives three groups: "Needs action" (nobody has acknowledged it, by severity then age), "Acknowledged, still open"
 * and "Resolved in the last 24 h", the last two collapsed. Each alert expands in place at `?alert=<id>`, and the status
 * line states the one count. Then "Recent activity": the five newest log entries and "Open the log ›". Operators
 * acknowledge with an optional note and resolve, stop all water from a fence breach and clear a mister latch; viewers
 * read the same page with none of those controls. No greenhouse data renders in SSR: it loads in the browser.
 */
export default function Activity() {
  const [params] = useSearchParams();
  const view = readView(params);
  const me = useMe();
  const shellNow = useShellNow();
  const { openAlerts, apiDown } = useShellStatus();
  // The water status says whether the irrigation firmware is on the controller (Clear fault) and names the lines.
  const water = panelRead(useApiRead("API-IRR-01", {}, { intervalMs: SLOW_POLL_MS }));
  const alerts = panelRead(useApiRead("API-14", RECENT_ALERTS, { intervalMs: ALERTS_POLL_MS }));
  const groups = groupAlerts(alerts.data?.items ?? []);
  const now = shellNow.data;
  const freshness = apiDown || !now ? null : now.freshness;
  const coverage = alertListEmptyText({ readOk: true, ingestor: freshness?.ingestor, climate: freshness?.climate });
  const loading = alerts.pending || (shellNow.pending && !now);
  // A failed read states no counts, even with an earlier answer kept: its rows may be gone (the groups say why).
  const counts = { open: openAlerts ?? groups.open.length, acknowledged: groups.acknowledged.length, resolved: groups.closed.length, coverage };
  const status = loading || alerts.failed ? null : alertsStatus(counts);
  return <>
    <PageHeader view="activity-alerts" status={status} statusShort={status === null ? null : alertsStatus({ ...counts, short: true })} />
    <div className="page act-alerts">
      <AlertGroups view={view} params={params} alerts={alerts} me={me} now={now} irrigation={water.failed ? null : water.data} loading={loading} />
      <RecentActivity me={me} />
    </div>
  </>;
}

// ---------------------------------------------------------------------------------------------------- the groups
type ItemContext = {
  readonly me: SignedIn; readonly now: Now | null; readonly irrigation: IrrigationStatus | null; readonly params: URLSearchParams; readonly expandedId: number | null;
  /** An Acknowledge on this page names its alert, so the acknowledged group opens to show who and when. */
  readonly onAcknowledged: (id: number) => void;
};

/**
 * "Needs action" while anything is unacknowledged (a failed read says "Alerts unavailable." with Retry, never that
 * nothing is open), then the acknowledged and resolved groups, collapsed, each opening when it holds the expanded alert
 * (`?resolved=24h` opens the resolved one). A linked alert in none of them says it is not open or resolved in 24 h.
 */
function AlertGroups({ view, params, alerts, me, now, irrigation, loading }: { view: ActivityView; params: URLSearchParams; alerts: PanelRead<AlertPage>; me: SignedIn; now: Now | null; irrigation: IrrigationStatus | null; loading: boolean }) {
  const groups = groupAlerts(alerts.data?.items ?? []);
  const expandedId = alertIdOf(view.alert);
  const [acknowledgedHere, setAcknowledgedHere] = useState<number | null>(null);
  const context: ItemContext = { me, now, irrigation, params, expandedId, onAcknowledged: setAcknowledgedHere };
  const item = (alert: Alert) => <AlertItem key={alert.id} alert={alert} context={context} />;
  const all = [...groups.open, ...groups.acknowledged, ...groups.closed];
  const state: PanelState = alerts.failed && !alerts.data ? "error" : loading ? "loading" : "ready";
  const linked = view.alert !== null && !loading && alerts.data && !all.some((row) => row.id === expandedId);
  // One block of the page: the groups at the card gap (an empty block takes no room). A read that failed after an
  // earlier answer says so above the rows it keeps, with Retry, as Now's Needs someone does: they may be gone.
  return <div className="act-groups">
    {alerts.failed && alerts.data && <p className="fault act-unavailable" data-alerts="unavailable">{ALERTS_UNAVAILABLE}: these may have changed. <Button onClick={alerts.retry}>Retry</Button></p>}
    {linked && <section className="act-linked" aria-labelledby="alert-linked-head">
      <h2 id="alert-linked-head">Linked alert</h2>
      <p>{notOpenText(view.alert ?? "")}</p>
    </section>}
    {(state !== "ready" || groups.open.length > 0) && <ListCard id="alerts" title="Needs action" thing="alerts" state={state} reserve="md" errorText={`${ALERTS_UNAVAILABLE}.`} onRetry={alerts.retry}>
      <ol className="alert-list act-alert-list">{groups.open.map(item)}</ol>
    </ListCard>}
    {groups.acknowledged.length > 0 && <AlertGroup summary={`Acknowledged, still open (${groups.acknowledged.length})`} holdsExpanded={groups.acknowledged.some((row) => row.id === expandedId || row.id === acknowledgedHere)}>
      {groups.acknowledged.map(item)}
    </AlertGroup>}
    {groups.closed.length > 0 && <AlertGroup summary={`Resolved in the last 24 h (${groups.closed.length})`} start={view.resolved} holdsExpanded={groups.closed.some((row) => row.id === expandedId)}>
      {groups.closed.map(item)}
    </AlertGroup>}
    {alerts.data?.next_cursor && <p className="meta">Showing the first {ALERT_LIMIT} alerts.</p>}
  </div>;
}

const notOpenText = (id: string) => `Alert ${id} is not open or resolved in the last 24 h`;

/**
 * A collapsed group ("Acknowledged, still open (2)"), open while it holds the expanded alert (a link to one, or an
 * Acknowledge that moved it here), then as the person leaves it. Its rows stay mounted, so a Clear fault readback or a
 * note in progress survives another alert being expanded or hidden.
 */
function AlertGroup({ summary, holdsExpanded, start = false, children }: { summary: string; holdsExpanded: boolean; start?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(holdsExpanded || start);
  const [wasHolding, setWasHolding] = useState(holdsExpanded);
  if (wasHolding !== holdsExpanded) {
    setWasHolding(holdsExpanded);
    if (holdsExpanded) setOpen(true);
  }
  return <Details className="alert-group card" open={open} onToggle={(event) => setOpen(event.currentTarget.open)} summary={summary}>
    <ol className="alert-list act-alert-list">{children}</ol>
  </Details>;
}

/**
 * One alert as `AlertRow` draws it, plus what its type needs: the flow alert's words and spigot guidance (it has no
 * Stop: no Verdify valve is open), the valve a fence breach opened with Stop all water, and Clear fault on a latched
 * mister while the irrigation firmware runs. Collapsed, it offers Acknowledge with an optional note; expanded (its
 * "Details ›", `?alert=<id>`), every field, the runbook as plain text, "Open in Graphs ±2 h" and the note that
 * Acknowledge and Resolve send.
 */
function AlertItem({ alert, context }: { alert: Alert; context: ItemContext }) {
  const { me, now, irrigation, params, expandedId, onAcknowledged } = context;
  const record = useRecordWrite();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [sending, setSending] = useState<"ack" | "resolve" | null>(null);
  const expanded = alert.id === expandedId;
  const title = words("alert_type", alert.alert_type);
  const closed = isClosed(alert);
  const unacknowledged = !closed && alert.status !== "acknowledged";
  const lineId = detailText(alert.details, "line_id");
  const breach = alert.alert_type === "irrigation_fence_breach" && !closed;
  const faulted = alert.alert_type === "irrigation_line_faulted" && !closed && lineId !== null && irrigation?.engine === true;
  const said = (kind: "ack" | "resolve", updated: Alert) => {
    const by = kind === "ack" ? updated.acknowledged_by : updated.resolved_by;
    const at = kind === "ack" ? updated.acknowledged_at : updated.resolved_at;
    toast.show({ message: `“${title}” ${kind === "ack" ? "acknowledged" : "resolved"}${by ? ` by ${personName(by, me)}` : ""}${at ? ` · ${shortTime(at)}` : ""}` });
  };
  const respond = async (kind: "ack" | "resolve", text: string | null) => {
    setSending(kind);
    const outcome = kind === "ack" ? await record.acknowledge(alert.id, text) : await record.resolve(alert.id, text);
    setSending(null);
    if (!outcome.ok) return;
    said(kind, outcome.data);
    if (kind === "ack") onAcknowledged(outcome.data.id);
    setNote("");
  };
  const detailId = `alert-${alert.id}-detail`;
  // Opening pushes, so Back closes it; closing replaces, so Back never reopens it (redesign §2.4).
  const toggle = <Link className="alert-open" to={expanded ? activityHref(params, { alert: null }) : activityHref(params, { alert: String(alert.id) })} preventScrollReset replace={expanded}
    aria-expanded={expanded} aria-controls={expanded ? detailId : undefined}>
    {expanded ? "Hide details" : <>Details <span className="screen-reader">of {title} </span><span aria-hidden="true">›</span></>}
  </Link>;
  // The API names people by username; the signed-in person reads by name ("Acknowledged by Jason · 06:47"). An open
  // flow alert's message is the ingestor's words for what LeakLines shows live, so the row says it once.
  const leakOpen = alert.alert_type === "leak_detected" && !closed;
  const named: Alert = { ...alert, acknowledged_by: alert.acknowledged_by ? personName(alert.acknowledged_by, me) : alert.acknowledged_by };
  const action = breach || faulted ? <>{breach && <StopAllWater />}{faulted && <ClearFault lineId={lineId} />}</> : undefined;
  return <AlertRow alert={named} title={title} id={`alert-${alert.id}`} details={toggle} action={action} message={!leakOpen}
    acknowledge={!expanded && unacknowledged ? <Acknowledge alert={alert} onAcknowledged={(updated) => { said("ack", updated); onAcknowledged(updated.id); }} /> : undefined}>
    {leakOpen && <LeakLines alert={alert} now={now} />}
    {alert.alert_type === "irrigation_fence_breach" && <ValveLine alert={alert} irrigation={irrigation} />}
    {closed && <ClosedLine alert={alert} me={me} />}
    {expanded && <div className="alert-detail" id={detailId}>
      <Kv items={alertFields(alert, me)} />
      <h4>Runbook</h4>
      {alert.runbook ? <p className="runbook">{alert.runbook}</p> : <p>No runbook for this alert type.</p>}
      <GraphLink firstSeen={alert.first_seen_at} />
      {!closed && <OperatorOnly><div className="alert-respond">
        <NoteField value={note} onChange={setNote} sentWith={unacknowledged ? "Acknowledge or Resolve" : "Resolve"} />
        <div className="actions">
          {unacknowledged && <Button write wide busy={sending === "ack"} disabled={record.sending} onClick={() => void respond("ack", note.trim() || null)}>Acknowledge</Button>}
          <Button write wide busy={sending === "resolve"} disabled={record.sending} onClick={() => void respond("resolve", note.trim() || null)}>Resolve</Button>
        </div>
        {record.error && <p className="field-error" role="alert">{record.error.message}</p>}
      </div></OperatorOnly>}
    </div>}
  </AlertRow>;
}

/**
 * The flow alert's answer for 3 a.m.: what the flow may be, the gallons in the last 10 min or "Flow stopped at HH:MM"
 * (never an all clear), and the spigot. Gallons and the stop time come from API-02 `leak` while it names this alert.
 */
function LeakLines({ alert, now }: { alert: Alert; now: Now | null }) {
  const leak = now && now.leak.alert_id === alert.id ? now.leak : null;
  const stoppedAt = leak?.stopped_at ?? detailText(alert.details, "stopped_at");
  const gallons = leak?.gal_last_10min;
  return <div className="act-leak">
    <p>A leak, or the hose, sink or hydro in use.</p>
    {stoppedAt ? <p>Flow stopped at <LocalTime at={stoppedAt} /></p>
      : gallons !== null && gallons !== undefined && <p>{formatValue(gallons, "gal")} in the last 10 min · <AgeLabel observedAt={now?.freshness.climate.observed_at} stale={now?.freshness.climate.stale ?? false} /></p>}
    <p className="alert-guidance">If no one is using water, close the spigot.</p>
  </div>;
}

/** The valve a fence breach opened (`details.equipment`): its line and, from API-IRR-01, the line's name. */
function ValveLine({ alert, irrigation }: { alert: Alert; irrigation: IrrigationStatus | null }) {
  const equipment = detailText(alert.details, "equipment");
  if (!equipment) return null;
  const line = lineByEquipment(equipment);
  const name = line ? irrigation?.lines.find((row) => row.line_id === line.id)?.name : undefined;
  return <p className="act-valve">Valve that opened: {line ? `${line.id} ${name ?? equipment}` : equipment}</p>;
}

/** Who closed an alert and when ("Resolved by Jason · 10:42"), with the resolution. */
function ClosedLine({ alert, me }: { alert: Alert; me: SignedIn }) {
  const by = alert.resolved_by ? ` by ${personName(alert.resolved_by, me)}` : "";
  return <p className="act-closed">{words("alert_status", alert.status)}{by}{alert.resolved_at && <> · <LocalTime at={alert.resolved_at} /></>}{alert.resolution && <> · “{alert.resolution}”</>}</p>;
}

/** "Open in Graphs ›": the window from 2 h before to 2 h after the alert was first seen. */
function GraphLink({ firstSeen }: { firstSeen: string }) {
  const first = Date.parse(firstSeen);
  const href = Number.isFinite(first) ? graphWindowUrl(first - GRAPH_MARGIN_MS, first + GRAPH_MARGIN_MS) : null;
  if (!href) return null;
  return <p className="alert-graph"><Link to={href}>Open in Graphs <span aria-hidden="true">›</span></Link> <span className="meta">±2 h around <LocalTime at={firstSeen} /></span></p>;
}

const ISO_TIME = /^\d{4}-\d\d-\d\dT\d\d:\d\d/;
function detailValue(value: unknown): ReactNode {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string") return ISO_TIME.test(value) && Number.isFinite(Date.parse(value)) ? <LocalTime at={value} date /> : value;
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return <code>{JSON.stringify(value)}</code>;
}
/**
 * Every field of an expanded alert, null as "—". The type and severity read through the words table like every other
 * code (an unknown one raw); `details` shows as stored.
 */
function alertFields(alert: Alert, me: SignedIn): KvItem[] {
  const details = Object.entries(alert.details ?? {});
  const by = (who: string | null | undefined, at: string | null | undefined) => (at ? <>{who ? `by ${personName(who, me)} · ` : ""}<LocalTime at={at} date /></> : null);
  const items: KvItem[] = [
    { term: "Type", value: words("alert_type", alert.alert_type) },
    { term: "Category", value: alert.category || null },
    { term: "Severity", value: words("alert_severity", alert.severity) },
    { term: "Message", value: alert.message || null },
    { term: "Zone", value: alert.zone || null },
    { term: "Value", value: alert.metric_value === null || alert.metric_value === undefined ? null : formatNumber(alert.metric_value) },
    { term: "Threshold", value: alert.threshold_value === null || alert.threshold_value === undefined ? null : formatNumber(alert.threshold_value) },
    { term: "Seen", value: <>{alert.count ?? 1} {alert.count === 1 || alert.count === undefined ? "time" : "times"}, first <LocalTime at={alert.first_seen_at} date />{alert.last_seen_at && <>, last <LocalTime at={alert.last_seen_at} date /></>}</> },
    { term: "Details", value: details.length ? <Kv className="kv-nested" items={details.map(([key, value]) => ({ key, term: key, value: detailValue(value) }))} /> : null },
    { term: "Acknowledged", value: by(alert.acknowledged_by, alert.acknowledged_at), reason: "not yet" },
    { term: "Notes", value: alert.notes || null },
  ];
  if (isClosed(alert)) items.push({ term: "Resolved", value: by(alert.resolved_by, alert.resolved_at) }, { term: "Resolution", value: alert.resolution || null });
  return items;
}

/**
 * The one optional note Acknowledge and Resolve send: one line of at most 500 characters, with a counter. `sentWith`
 * names the buttons the row offers ("Acknowledge or Resolve", or "Resolve" once acknowledged).
 */
function NoteField({ value, onChange, sentWith }: { value: string; onChange: (text: string) => void; sentWith: string }) {
  const ids = useFieldIds();
  const writesEnabled = useWritesEnabled();
  return <div className="field note-field">
    <label htmlFor={ids.id}>Note (optional)</label>
    <input id={ids.id} type="text" maxLength={REASON_MAX} autoComplete="off" value={value} disabled={!writesEnabled} aria-describedby={ids.hintId}
      onChange={(event) => onChange(event.target.value)} />
    <FieldHint id={ids.hintId}>{value.length}/{REASON_MAX} characters, sent with {sentWith}</FieldHint>
  </div>;
}

/** Clear fault on a latched mister: API-IRR-13 `{line_id}`, then the readback (C): the latch gone from API-IRR-01. */
function ClearFault({ lineId }: { lineId: string }) {
  const command = useCommand({ operation: "API-IRR-13" });
  const note = command.phase === "waiting" ? "Waiting for the controller" : command.phase === "done" ? "Latch cleared" : command.phase === "late" ? command.message : null;
  return <>
    <Button write busy={command.sending} onClick={() => void command.send({}, { line_id: lineId })}>Clear fault <span className="screen-reader">on {lineId}</span></Button>
    {note && <span className="meta">{note}</span>}
    {command.error && <span className="field-error" role="alert">{command.error.message}</span>}
  </>;
}

// ---------------------------------------------------------------------------------------------------- recent activity
/** The five newest log entries (API-17, every 60 s), each one line with its date when not today, and "Open the log ›". */
function RecentActivity({ me }: { me: SignedIn }) {
  const log = panelRead(useApiRead("API-17", RECENT_ACTIVITY, { intervalMs: SLOW_POLL_MS }));
  const settings = useApiRead("API-09", {}, { intervalMs: SLOW_POLL_MS });
  const catalog = useMemo(() => catalogOf(settings.data?.items), [settings.data]);
  const entries = log.data?.items ?? [];
  const today = useClockValue((clock) => localDate(new Date(clock).toISOString()));
  const state: PanelState = log.failed && !log.data ? "error" : log.pending ? "loading" : entries.length ? "ready" : "empty";
  return <ListCard id="recent-activity" title="Recent activity" thing="recent activity" state={state} reserve="sm" onRetry={log.retry}
    link={{ to: "/activity/log", label: "Open the log" }} empty={{ message: "No activity yet." }}>
    {log.failed && log.data && <p className="fault act-unavailable">Couldn't load the newest activity: these entries may be old. <Button onClick={log.retry}>Retry</Button></p>}
    <ol className="act-entries act-recent">{entries.map((entry) => <EventRow key={entry.id} entry={entry} catalog={catalog} me={me} showDate={localDate(entry.ts) !== today} />)}</ol>
  </ListCard>;
}
