// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ReadbackBadge } from "../components/badge";
import { Button } from "../components/button";
import { LocalTime } from "../components/common";
import { Details } from "../components/details";
import { EventRow } from "../components/event-row";
import { PageHeader } from "../components/layout/page-header";
import { EmptyState, Panel, type PanelState } from "../components/panel";
import {
  ACTION_FILTERS, LOG_FILTERS, SOURCE_FILTERS, catalogOf, entryFacts, entryTitle, logHref, personName, planChangeText, planChanges, readView, seenActors,
  seenAgents, shownActor, type ActivityView, type Catalog, type LogFilter, type LogFilters, type SignedIn,
} from "../lib/activity";
import { localDate } from "../lib/format";
import { apiGet, type ActivityEntry, type Parsed } from "../lib/operator-api";
import { readKey, useApiRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";
import { useMe } from "../lib/shell";
import { usePolledQuery } from "../lib/use-current";

export const meta = () => viewMeta("activity-log");

/** The log and the settings catalog every 60 s (screens §7). */
const LOG_POLL_MS = 60_000;
const CATALOG_POLL_MS = 60_000;

/**
 * Activity → Log (`/activity/log`, redesign §5.9, D-UX-03): every write by a person, Iris, an agent or the system,
 * newest first, from API-17 with its filters in the URL (`source`, `actor`, `action`, `target`). Moved from the lower
 * half of `/activity` unchanged; an old `/activity?target=L01` link redirects here with its query. The log writes
 * nothing. No greenhouse data renders in SSR: it loads in the browser.
 */
export default function ActivityLog() {
  const [params] = useSearchParams();
  const view = readView(params);
  return <>
    <PageHeader view="activity-log" />
    <LogPanel view={view} params={params} />
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
  const clearHref = logHref(params, Object.fromEntries(LOG_FILTERS.map((name) => [name, null])));
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
  const go = (name: LogFilter, value: string) => navigate(logHref(params, { [name]: value.trim() || null }), { preventScrollReset: true });
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
