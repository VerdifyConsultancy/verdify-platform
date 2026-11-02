// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ALERTS_UNAVAILABLE, AlertRow, alertListEmptyText } from "../components/alert-row";
import { ReadbackBadge } from "../components/badge";
import { Button } from "../components/button";
import { LocalTime, PageHead } from "../components/common";
import { Details } from "../components/details";
import { EventRow } from "../components/event-row";
import { FieldHint, useFieldIds } from "../components/forms";
import { AgeLabel } from "../components/freshness";
import { Kv, type KvItem } from "../components/kv";
import { EmptyState, Panel, PanelError, type PanelState } from "../components/panel";
import { useToast } from "../components/toast";
import {
  ACTION_FILTERS, LOG_FILTERS, SOURCE_FILTERS, activityHref, alertIdOf, catalogOf, detailText, entryFacts, entryTitle, groupAlerts, isClosed,
  personName, planChangeText, planChanges, readView, seenActors, seenAgents, shownActor, RESOLVED_24H, type ActivityView, type Catalog, type LogFilter,
  type LogFilters, type SignedIn,
} from "../lib/activity";
import { formatNumber, formatValue, localDate, shortTime } from "../lib/format";
import { graphWindowUrl } from "../lib/graph-link";
import { lineByEquipment } from "../lib/lines";
import { apiGet, type ActivityEntry, type Alert, type IrrigationStatus, type Now, type Parsed } from "../lib/operator-api";
import { readKey, useApiRead } from "../lib/queries";
import { OperatorOnly, useWritesEnabled } from "../lib/session";
import { useMe, useShellNow, useShellStatus } from "../lib/shell";
import { usePolledQuery } from "../lib/use-current";
import { words } from "../lib/words";
import { REASON_MAX, useCommand, useRecordWrite, useStop } from "../lib/writes";

export const meta = () => [{ title: "Activity · Verdify dashboard" }];

/** Open alerts every 15 s; the log, the water status and the settings catalog every 60 s (screens §7). */
const ALERTS_POLL_MS = 15_000;
const LOG_POLL_MS = 60_000;
const ENGINE_POLL_MS = 60_000;
const CATALOG_POLL_MS = 60_000;
/** API-14's largest page: the merged open alerts fit in one. */
const ALERT_LIMIT = 200;
/** "Open in Graphs" shows 2 h either side of an alert's first seen. */
const GRAPH_MARGIN_MS = 2 * 3_600_000;

/**
 * SCR-06 Activity and alerts (screens §7): the open alerts (API-14), each expanding in place at `?alert=<id>`, then the
 * activity log (API-17) with its filters, every view state in the URL. Operators acknowledge and resolve with an
 * optional note, stop all water from a fence breach and clear a mister latch; viewers read the same page with none of
 * those controls. No greenhouse data renders in SSR: it loads in the browser.
 */
export default function Activity() {
  const [params] = useSearchParams();
  const view = readView(params);
  // The water status says whether the irrigation firmware is on the controller (Clear fault) and names the lines.
  const water = useApiRead("API-IRR-01", {}, { intervalMs: ENGINE_POLL_MS });
  const irrigation = water.status === "success" ? water.data ?? null : null;
  return <>
    <PageHead eyebrow="Activity · people, Iris, agents and system · America/Denver" title="Activity and alerts" />
    <AlertsPanel view={view} params={params} irrigation={irrigation} />
    <LogPanel view={view} params={params} />
  </>;
}

// ---------------------------------------------------------------------------------------------------- open alerts
type AlertItemProps = {
  alert: Alert;
  expanded: boolean;
  /** The row's link that expands it, or collapses it when it is expanded. */
  toggleHref: string;
  me: SignedIn;
  now: Now | null;
  irrigation: IrrigationStatus | null;
};

/**
 * The open alerts, polled every 15 s: the unacknowledged rows in API order under "Open alerts · N" (N is API-02's one
 * count, the nav's), "Acknowledged, still open (N)" collapsed, and the alerts resolved in the last 24 h while
 * `resolved=24h`. The list says "No open alerts" only after a good read while the ingestor and climate are fresh, and a
 * failed read says "Alerts unavailable" here alone. A linked alert that is no longer open loads from `status=recent`.
 */
function AlertsPanel({ view, params, irrigation }: { view: ActivityView; params: URLSearchParams; irrigation: IrrigationStatus | null }) {
  const me = useMe();
  const shellNow = useShellNow();
  const { openAlerts, apiDown } = useShellStatus();
  const alerts = useApiRead("API-14", { status: view.resolved ? "recent" : "open", limit: ALERT_LIMIT }, { intervalMs: ALERTS_POLL_MS });
  const rows = alerts.data?.items ?? [];
  const groups = groupAlerts(rows);
  const expandedId = alertIdOf(view.alert);
  const shown = [...groups.open, ...groups.acknowledged, ...(view.resolved ? groups.closed : [])];
  const now = shellNow.data;
  const freshness = apiDown || !now ? null : now.freshness;
  const collapseHref = activityHref(params, { alert: null });
  const item = (alert: Alert) => <AlertItem key={alert.id} alert={alert} expanded={alert.id === expandedId} me={me} now={now} irrigation={irrigation}
    toggleHref={alert.id === expandedId ? collapseHref : activityHref(params, { alert: String(alert.id) })} />;
  const state: PanelState = alerts.isError ? "error" : alerts.isPending || shellNow.pending ? "loading" : "ready";
  const resolvedToggle = <Link className="panel-link" preventScrollReset to={activityHref(params, { resolved: view.resolved ? null : RESOLVED_24H })}>
    {view.resolved ? "Hide resolved" : "Show resolved in the last 24 h"} <span aria-hidden="true">›</span>
  </Link>;
  // The linked alert's row as the list last held it, shown while status=recent answers for a just-resolved alert.
  const held = rows.find((row) => row.id === expandedId);
  const lastHeld = useRef<Alert | null>(null);
  if (held) lastHeld.current = held;
  let linked: ReactNode = null;
  if (view.alert !== null && !shown.some((row) => row.id === expandedId)) {
    // A resolved alert the open list still holds (just resolved here), else one only status=recent may have.
    linked = <section className="alert-linked" aria-labelledby="alert-linked-head">
      <h3 id="alert-linked-head">Linked alert</h3>
      {held ? <ol className="events alert-list">{item(held)}</ol>
        : expandedId === null || view.resolved ? <p>{notOpenText(view.alert)}</p>
        : <RecentAlert id={expandedId} known={lastHeld.current?.id === expandedId ? lastHeld.current : null} item={item} />}
    </section>;
  }
  return <Panel id="alerts" title={<>Open alerts{openAlerts !== null && ` · ${openAlerts}`}</>} thing="alerts" state={state} reserve="md"
    errorText={`${ALERTS_UNAVAILABLE}.`} onRetry={() => void alerts.refetch()} actions={resolvedToggle}>
    {linked}
    {groups.open.length ? <ol className="events alert-list">{groups.open.map(item)}</ol>
      : <p className="alert-empty">{alertListEmptyText({ readOk: true, ingestor: freshness?.ingestor, climate: freshness?.climate })}</p>}
    {groups.acknowledged.length > 0 && <AcknowledgedGroup rows={groups.acknowledged} holdsExpanded={groups.acknowledged.some((row) => row.id === expandedId)} item={item} />}
    {view.resolved && <section className="alert-group" aria-labelledby="alert-resolved-head">
      <h3 id="alert-resolved-head">Resolved in the last 24 h</h3>
      {groups.closed.length ? <ol className="events alert-list">{groups.closed.map(item)}</ol> : <p className="alert-empty">Nothing resolved in the last 24 h.</p>}
    </section>}
    {alerts.data?.next_cursor && <p className="state-note">Showing the first {ALERT_LIMIT} alerts.</p>}
  </Panel>;
}

const notOpenText = (id: string) => `Alert ${id} is not open or resolved in the last 24 h`;

/**
 * "Acknowledged, still open (N)", collapsed until it holds the expanded alert (a link to one, or an Acknowledge that
 * moved it here), then as the person leaves it. Its rows stay mounted, so a Clear fault readback or a note in progress
 * survives another alert being expanded or hidden.
 */
function AcknowledgedGroup({ rows, holdsExpanded, item }: { rows: readonly Alert[]; holdsExpanded: boolean; item: (alert: Alert) => ReactNode }) {
  const [open, setOpen] = useState(holdsExpanded);
  const [wasHolding, setWasHolding] = useState(holdsExpanded);
  if (wasHolding !== holdsExpanded) {
    setWasHolding(holdsExpanded);
    if (holdsExpanded) setOpen(true);
  }
  return <Details className="alert-group" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}
    summary={`Acknowledged, still open (${rows.length})`}>
    <ol className="events alert-list">{rows.map(item)}</ol>
  </Details>;
}

/**
 * A linked alert missing from the open rows: API-14 `status=recent` has it while it was resolved in the last 24 h.
 * Until that read answers, the row the open list last held (`known`) stands in, so a Resolve never flickers.
 */
function RecentAlert({ id, known, item }: { id: number; known: Alert | null; item: (alert: Alert) => ReactNode }) {
  const recent = useApiRead("API-14", { status: "recent", limit: ALERT_LIMIT }, { intervalMs: ALERTS_POLL_MS });
  const found = recent.data?.items.find((row) => row.id === id) ?? (recent.isPending ? known ?? undefined : undefined);
  if (found) return <ol className="events alert-list">{item(found)}</ol>;
  if (recent.isError) return <PanelError thing={`alert ${id}`} onRetry={() => void recent.refetch()} />;
  if (recent.isPending) return <p className="state-note">Loading alert {id}</p>;
  return <p>{notOpenText(String(id))}</p>;
}

/**
 * One alert as `AlertRow` draws it, plus what its type needs: the flow alert's words and spigot guidance (it has no
 * Stop: no Verdify valve is open), the valve a fence breach opened with Stop all water, and Clear fault on a latched
 * mister while the irrigation firmware runs. Expanded, it shows every field, the runbook as plain text, "Open in
 * Graphs" and the note that Acknowledge and Resolve send; a collapsed row's Acknowledge sends no note.
 */
function AlertItem({ alert, expanded, toggleHref, me, now, irrigation }: AlertItemProps) {
  const record = useRecordWrite();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [sending, setSending] = useState<"ack" | "resolve" | null>(null);
  const title = words("alert_type", alert.alert_type);
  const closed = isClosed(alert);
  const unacknowledged = !closed && alert.status !== "acknowledged";
  const lineId = detailText(alert.details, "line_id");
  const breach = alert.alert_type === "irrigation_fence_breach" && !closed;
  const faulted = alert.alert_type === "irrigation_line_faulted" && !closed && lineId !== null && irrigation?.engine === true;
  const respond = async (kind: "ack" | "resolve", text: string | null) => {
    setSending(kind);
    const outcome = kind === "ack" ? await record.acknowledge(alert.id, text) : await record.resolve(alert.id, text);
    setSending(null);
    if (!outcome.ok) return;
    const by = kind === "ack" ? outcome.data.acknowledged_by : outcome.data.resolved_by;
    const at = kind === "ack" ? outcome.data.acknowledged_at : outcome.data.resolved_at;
    toast.show({ message: `“${title}” ${kind === "ack" ? "acknowledged" : "resolved"}${by ? ` by ${personName(by, me)}` : ""}${at ? ` · ${shortTime(at)}` : ""}` });
    setNote("");
  };
  const quick = (!expanded && unacknowledged) || breach || faulted ? <>
    {!expanded && unacknowledged && <Button write wide busy={sending === "ack"} disabled={record.sending} onClick={() => void respond("ack", null)}>
      Acknowledge <span className="screen-reader">{title}</span>
    </Button>}
    {!expanded && record.error && <span className="field-error" role="alert">{record.error.message}</span>}
    {breach && <StopAllWater />}
    {faulted && <ClearFault lineId={lineId} />}
  </> : undefined;
  const detailId = `alert-${alert.id}-detail`;
  // The API names people by username; the signed-in person reads by name ("Acknowledged by Jason · 06:47"). An open
  // flow alert's message is the ingestor's words for what LeakLines shows live ("… since 03:02", "Flow stopped at
  // 03:20"), so the row says it once; the expanded fields keep the message.
  const leakOpen = alert.alert_type === "leak_detected" && !closed;
  const named: Alert = {
    ...alert, message: leakOpen ? "" : alert.message,
    acknowledged_by: alert.acknowledged_by ? personName(alert.acknowledged_by, me) : alert.acknowledged_by,
  };
  return <AlertRow alert={named} title={title} id={`alert-${alert.id}`} acknowledge={quick}>
    {leakOpen && <LeakLines alert={alert} now={now} />}
    {alert.alert_type === "irrigation_fence_breach" && <ValveLine alert={alert} irrigation={irrigation} />}
    {closed && <ClosedLine alert={alert} me={me} />}
    <p className="alert-toggle"><Link to={toggleHref} preventScrollReset aria-expanded={expanded} aria-controls={expanded ? detailId : undefined}>
      {expanded ? "Hide details" : <>Details <span className="screen-reader">of {title}</span> <span aria-hidden="true">›</span></>}
    </Link></p>
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
  return <>
    <p>A leak, or the hose, sink or hydro in use.</p>
    {stoppedAt ? <p>Flow stopped at <LocalTime at={stoppedAt} /></p>
      : gallons !== null && gallons !== undefined && <p>{formatValue(gallons, "gal")} in the last 10 min · <AgeLabel observedAt={now?.freshness.climate.observed_at} stale={now?.freshness.climate.stale ?? false} /></p>}
    <p className="alert-guidance">If no one is using water, close the spigot.</p>
  </>;
}

/** The valve a fence breach opened (`details.equipment`): its line and, from API-IRR-01, the line's name. */
function ValveLine({ alert, irrigation }: { alert: Alert; irrigation: IrrigationStatus | null }) {
  const equipment = detailText(alert.details, "equipment");
  if (!equipment) return null;
  const line = lineByEquipment(equipment);
  const name = line ? irrigation?.lines.find((row) => row.line_id === line.id)?.name : undefined;
  return <p>Valve that opened: {line ? `${line.id} ${name ?? equipment}` : equipment}</p>;
}

/** Who closed an alert and when ("Resolved by Jason · 10:42"), with the resolution. */
function ClosedLine({ alert, me }: { alert: Alert; me: SignedIn }) {
  const by = alert.resolved_by ? ` by ${personName(alert.resolved_by, me)}` : "";
  return <>
    <p>{words("alert_status", alert.status)}{by}{alert.resolved_at && <> · <LocalTime at={alert.resolved_at} /></>}</p>
    {alert.resolution && <p>“{alert.resolution}”</p>}
  </>;
}

/** "Open in Graphs ›": the window from 2 h before to 2 h after the alert was first seen. */
function GraphLink({ firstSeen }: { firstSeen: string }) {
  const first = Date.parse(firstSeen);
  const href = Number.isFinite(first) ? graphWindowUrl(first - GRAPH_MARGIN_MS, first + GRAPH_MARGIN_MS) : null;
  if (!href) return null;
  return <p className="alert-graph"><Link to={href}>Open in Graphs <span aria-hidden="true">›</span></Link> <span className="state-note">±2 h around <LocalTime at={firstSeen} /></span></p>;
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

/**
 * Stop all water on a fence breach: API-IRR-07 `{}` through `useStop`, one tap with no dialog, never disabled by the
 * writes flag (the BFF passes it), then the Stop result toast once the relays read OFF or 30 s pass.
 */
function StopAllWater() {
  const stopper = useStop();
  return <>
    <Button variant="stop" write wide busy={stopper.sending} onClick={() => void stopper.stop()}>Stop all water</Button>
    {stopper.confirming && <span className="state-note">Waiting for the relays to read OFF</span>}
  </>;
}

/** Clear fault on a latched mister: API-IRR-13 `{line_id}`, then the readback (C): the latch gone from API-IRR-01. */
function ClearFault({ lineId }: { lineId: string }) {
  const command = useCommand({ operation: "API-IRR-13" });
  const note = command.phase === "waiting" ? "Waiting for the controller" : command.phase === "done" ? "Latch cleared" : command.phase === "late" ? command.message : null;
  return <>
    <Button write wide busy={command.sending} onClick={() => void command.send({}, { line_id: lineId })}>Clear fault <span className="screen-reader">on {lineId}</span></Button>
    {note && <span className="state-note">{note}</span>}
    {command.error && <span className="field-error" role="alert">{command.error.message}</span>}
  </>;
}

// ---------------------------------------------------------------------------------------------------- the log
type LogPages = { readonly items: readonly ActivityEntry[]; readonly next_cursor: string | null; readonly pages: number };

/** Up to `pages` API-17 pages in order, each from the previous one's `next_cursor`, as one list. */
async function fetchLog(filters: LogFilters, pages: number, signal: AbortSignal): Promise<LogPages> {
  const items: ActivityEntry[] = [];
  const seen = new Set<number>();
  let cursor: string | null = null;
  let fetched = 0;
  do {
    const page: Parsed<"API-17"> = await apiGet("API-17", cursor === null ? { ...filters } : { ...filters, cursor }, { signal });
    fetched += 1;
    for (const entry of page.items) if (!seen.has(entry.id)) { seen.add(entry.id); items.push(entry); }
    cursor = page.next_cursor;
  } while (cursor !== null && fetched < pages);
  return { items, next_cursor: cursor, pages: fetched };
}

/**
 * The log with its filters, polled every 60 s. "Load older" asks for one more page, and every poll reads all the pages
 * shown again from the newest, so a refresh never leaves a gap between them. The key sits under API-17's, so a write
 * hook's refresh of API-17 reaches it.
 */
function useActivityLog(filters: LogFilters) {
  const filterKey = JSON.stringify(filters);
  const [paging, setPaging] = useState({ key: filterKey, pages: 1 });
  const pages = paging.key === filterKey ? paging.pages : 1;
  const query = usePolledQuery<LogPages>([...readKey("API-17", filters), "log"], (signal) => fetchLog(filters, pages, signal), LOG_POLL_MS);
  const { refetch } = query;
  useEffect(() => { if (pages > 1) void refetch(); }, [pages, refetch]);
  return {
    query,
    loadingOlder: query.isFetching && pages > (query.data?.pages ?? 0),
    loadOlder: () => setPaging({ key: filterKey, pages: pages + 1 }),
  };
}

/** The activity log: every write by a person, Iris, an agent or the system, newest first. */
function LogPanel({ view, params }: { view: ActivityView; params: URLSearchParams }) {
  const me = useMe();
  const { query, loadingOlder, loadOlder } = useActivityLog(view.filters);
  const settings = useApiRead("API-09", {}, { intervalMs: CATALOG_POLL_MS });
  const catalog = useMemo(() => catalogOf(settings.data?.items), [settings.data]);
  const entries = query.data?.items ?? [];
  const filtered = LOG_FILTERS.some((name) => view.filters[name] !== undefined);
  const clearHref = activityHref(params, Object.fromEntries(LOG_FILTERS.map((name) => [name, null])));
  const state: PanelState = query.isError ? "error" : query.isPending ? "loading" : entries.length ? "ready" : "empty";
  const today = localDate(new Date().toISOString());
  const empty = filtered
    ? <EmptyState message="No activity matches these filters" widen={{ to: clearHref, label: "Clear filters" }} />
    : <EmptyState message="No activity yet." detail="Every change by a person, Iris, an agent or the system shows here." />;
  return <Panel id="log" title="Activity log" thing="activity" state={state} reserve="lg" onRetry={() => void query.refetch()} empty={empty}
    actions={<LogFilterForm view={view} params={params} entries={entries} me={me} clearHref={filtered ? clearHref : null} />}>
    <ol className="events activity-log">{entries.map((entry) => <LogEntry key={entry.id} entry={entry} catalog={catalog} me={me} today={today} />)}</ol>
    {query.data?.next_cursor && <div className="actions"><Button wide disabled={loadingOlder} onClick={loadOlder}>{loadingOlder ? "Loading older…" : "Load older"}</Button></div>}
  </Panel>;
}

/**
 * One write as an `EventRow`: the time, the writer in words (AI for Iris and the agents only), the action and its
 * target, what changed, the reason in quotes and the readback outcome. An Iris plan of several changes lists them in a
 * disclosure.
 */
function LogEntry({ entry, catalog, me, today }: { entry: ActivityEntry; catalog: Catalog; me: SignedIn; today: string }) {
  const day = localDate(entry.ts);
  const changes = entry.action === "plan.write" ? planChanges(entry.detail) : [];
  const facts = entryFacts(entry, catalog);
  return <EventRow at={entry.ts} showDate={day !== today} title={entryTitle(entry, catalog)} id={`activity-${entry.id}`}
    badges={entry.state ? <><ReadbackBadge state={entry.state} />{entry.confirmed_at && <> <LocalTime at={entry.confirmed_at} /></>}</> : undefined}
    actor={shownActor(entry.actor, me)} detail={facts.length ? facts.join(" · ") : undefined} reason={entry.reason ? `“${entry.reason}”` : undefined}>
    {changes.length > 1 && <Details summary={`${changes.length} changes`}>
      <ul className="plan-changes">{changes.map((change, index) => <li key={index}>{planChangeText(change, catalog, day)}</li>)}</ul>
    </Details>}
  </EventRow>;
}

/** An option for every value the URL may hold, so the select always shows the filter in force. */
const withCurrent = (values: readonly string[], current: string) => (current && !values.includes(current) ? [...values, current] : values);

/**
 * Source, actor, action and target, straight into the URL and on to API-17: a select applies at once, the target on
 * Apply or Enter. Clear filters keeps the rest of the view (the expanded alert, resolved).
 */
function LogFilterForm({ view, params, entries, me, clearHref }: { view: ActivityView; params: URLSearchParams; entries: readonly ActivityEntry[]; me: SignedIn; clearHref: string | null }) {
  const navigate = useNavigate();
  const go = (name: LogFilter, value: string) => navigate(activityHref(params, { [name]: value.trim() || null }), { preventScrollReset: true });
  const { source = "", actor = "", action = "", target = "" } = view.filters;
  const sources = withCurrent([...SOURCE_FILTERS, ...seenAgents(entries)], source);
  const actors = withCurrent(seenActors(entries), actor);
  const actions = withCurrent(ACTION_FILTERS.map(([value]) => value), action);
  const actionLabel = (value: string) => ACTION_FILTERS.find(([prefix]) => prefix === value)?.[1] ?? value;
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = new FormData(event.currentTarget).get("target");
    go("target", typeof value === "string" ? value : "");
  };
  return <form className="filters log-filters" role="search" aria-label="Activity log filters" onSubmit={submit}>
    <label>Source<select name="source" value={source} onChange={(event) => go("source", event.target.value)}>
      <option value="">All</option>
      {sources.map((value) => <option key={value} value={value}>{value}</option>)}
    </select></label>
    <label>Actor<select name="actor" value={actor} onChange={(event) => go("actor", event.target.value)}>
      <option value="">All</option>
      {actors.map((name) => <option key={name} value={name}>{personName(name, me)}</option>)}
    </select></label>
    <label>Action<select name="action" value={action} onChange={(event) => go("action", event.target.value)}>
      <option value="">All</option>
      {actions.map((value) => <option key={value} value={value}>{actionLabel(value)}</option>)}
    </select></label>
    <label>Target<input key={target} name="target" type="search" defaultValue={target} autoComplete="off" placeholder="L01 or mister_engage_kpa" /></label>
    <button type="submit" className="btn">Apply</button>
    {clearHref && <Link to={clearHref} preventScrollReset>Clear filters</Link>}
  </form>;
}
