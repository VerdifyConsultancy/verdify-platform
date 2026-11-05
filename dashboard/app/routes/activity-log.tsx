// SPDX-License-Identifier: MIT
import { useEffect, useId, useMemo, useState, type FocusEvent, type KeyboardEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { CsvLink } from "../components/activity/csv-link";
import { Button } from "../components/button";
import { Chips, type ChipLink } from "../components/chips";
import { EventRow } from "../components/event-row";
import { useClockValue } from "../components/freshness";
import { PageHeader } from "../components/layout/page-header";
import { EmptyState, PanelError } from "../components/panel";
import {
  DATE_FILTERS, LOG_FILTERS, WHAT_FACETS, WHO_FACETS, catalogOf, dayHeading, entriesByDay, facetsWith, isDay, logCsv, logCsvName, logHref, logQuery, personName,
  readView, type ActivityView, type Catalog, type Facet, type SignedIn,
} from "../lib/activity";
import { localDate } from "../lib/format";
import { lineById, lineByName, lineDisplayName } from "../lib/lines";
import { apiGet, type ActivityEntry, type Parsed } from "../lib/operator-api";
import { readKey, useApiRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";
import { useMe } from "../lib/shell";
import { usePolledQuery } from "../lib/use-current";

export const meta = () => viewMeta("activity-log");

/** The log and the settings catalog every 60 s (screens §7). */
const LOG_POLL_MS = 60_000;
const CATALOG_POLL_MS = 60_000;
export const LOG_STATUS = "Every write by people, Iris, agents and the system";

/**
 * Activity → Log (`/activity/log`, redesign §5.9, D-UX-03): every write by a person, Iris, an agent or the system,
 * newest first, from API-17, one line an entry under sticky day headers. Who and What facets, a Target and From and To
 * dates narrow it, each applied on change and kept in the URL (`source`, `action`, `target`, `from`, `to`; an old
 * `actor` filter shows as a chip that clears it); an old `/activity?target=L01` link redirects here with its query. An
 * Iris plan collapses to one entry that expands to its changes, and "CSV of these entries" saves the loaded entries. The
 * log writes nothing. No greenhouse data renders in SSR: it loads in the browser.
 */
export default function ActivityLog() {
  const [params] = useSearchParams();
  const view = readView(params);
  const me = useMe();
  const { query, loadingOlder, loadOlder } = useActivityLog(logQuery(view));
  const settings = useApiRead("API-09", {}, { intervalMs: CATALOG_POLL_MS });
  const catalog = useMemo(() => catalogOf(settings.data?.items), [settings.data]);
  const entries = query.data?.items ?? [];
  return <>
    <PageHeader view="activity-log" status={LOG_STATUS} />
    <div className="page act-log-page">
      <LogFilters view={view} params={params} me={me} />
      <LogList view={view} params={params} entries={entries} catalog={catalog} me={me} failed={query.isError && !query.data} pending={query.isPending}
        retry={() => void query.refetch()} more={query.data?.next_cursor ? { loading: loadingOlder, load: loadOlder } : null} />
    </div>
  </>;
}

// ---------------------------------------------------------------------------------------------------- the log
type LogPages = { readonly items: readonly ActivityEntry[]; readonly next_cursor: string | null; readonly pages: number };
type LogParams = ReturnType<typeof logQuery>;

/** Up to `pages` API-17 pages in order, each from the previous one's `next_cursor`, as one list. */
async function fetchLog(filters: LogParams, pages: number, signal: AbortSignal): Promise<LogPages> {
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
function useActivityLog(filters: LogParams) {
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

type LogListProps = {
  view: ActivityView; params: URLSearchParams; entries: readonly ActivityEntry[]; catalog: Catalog; me: SignedIn;
  failed: boolean; pending: boolean; retry: () => void; more: { loading: boolean; load: () => void } | null;
};

/** The entries under their day headers (Today, Yesterday, Mon 3 Nov), each header sticking while its day scrolls. */
function LogList({ view, params, entries, catalog, me, failed, pending, retry, more }: LogListProps) {
  const today = useClockValue((clock) => localDate(new Date(clock).toISOString()));
  const headId = useId();
  const filtered = LOG_FILTERS.some((name) => view.filters[name] !== undefined) || DATE_FILTERS.some((name) => view.dates[name] !== undefined);
  const clearHref = logHref(params, Object.fromEntries([...LOG_FILTERS, ...DATE_FILTERS].map((name) => [name, null])));
  let body;
  if (pending) body = <><div className="skeleton skeleton-lg" aria-hidden="true" /><p className="screen-reader">Loading activity</p></>;
  else if (failed) body = <PanelError thing="activity" onRetry={retry} />;
  else if (!entries.length) {
    body = filtered
      ? <EmptyState message="No activity matches these filters" widen={{ to: clearHref, label: "Clear filters" }} />
      : <EmptyState message="No activity yet." detail="Every change by a person, Iris, an agent or the system shows here." />;
  } else {
    body = <>
      {entriesByDay(entries).map((day) => <section key={day.date} className="act-day" aria-labelledby={`${headId}-${day.date}`}>
        <h3 id={`${headId}-${day.date}`} className="act-day-head">{dayHeading(day.date, today)}</h3>
        <ol className="act-entries">{day.entries.map((entry) => <EventRow key={entry.id} entry={entry} catalog={catalog} me={me} id={`activity-${entry.id}`} />)}</ol>
      </section>)}
      {more && <div className="act-more"><Button wide disabled={more.loading} onClick={more.load}>{more.loading ? "Loading older…" : "Load older"}</Button></div>}
    </>;
  }
  return <section className="card act-log" aria-label="Activity log" aria-busy={pending || undefined}>
    <div className="act-log-head">
      <p className="meta act-log-count">{pending || failed ? "" : `${entries.length} ${entries.length === 1 ? "entry" : "entries"}${more ? " loaded" : ""}`}</p>
      {entries.length > 0 && <CsvLink filename={logCsvName(entries)} label="CSV of these entries" version={entries} build={() => logCsv(entries, catalog, me)} />}
    </div>
    {body}
  </section>;
}

// ---------------------------------------------------------------------------------------------------- the filters
/** The facet's chips (from 861 px): All, then each value, the one in force current. */
function facetChips(params: URLSearchParams, name: "source" | "action", facets: readonly Facet[], current: string | undefined): ChipLink[] {
  return [
    { key: "all", label: "All", to: logHref(params, { [name]: null }), current: !current, keepScroll: true },
    ...facets.map(([value, label]) => ({ key: value, label, to: logHref(params, { [name]: value }), current: current === value, keepScroll: true })),
  ];
}

/**
 * A field that edits a URL value: it holds what is typed, and takes the URL's value again whenever that changes (a
 * filter applied, Clear filters, Back), so applying a filter never remounts the field that has focus.
 */
function useUrlField(value: string): [string, (next: string) => void] {
  const [field, setField] = useState(value);
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    setField(value);
  }
  return [field, setField];
}

/**
 * A whole day a person means: typing a year fills it one digit at a time, and "0002-11-02" is already a valid date, so
 * only a year from 2000 counts before the field is left.
 */
const meantDay = (value: string) => isDay(value) && Number(value.slice(0, 4)) >= 2000;

/**
 * Who and What as chips from 861 px and as selects at 860 px and below (both render; the stylesheet shows one), a Target
 * applied when it is left or Enter is pressed, and From and To dates applied as soon as a whole day is picked or typed
 * (a year from 2000), else when the field is left or Enter is pressed. Every filter lands in the URL and on to API-17,
 * and the fields keep focus while it does. "Clear filters" keeps the rest of the view.
 */
function LogFilters({ view, params, me }: { view: ActivityView; params: URLSearchParams; me: SignedIn }) {
  const navigate = useNavigate();
  const go = (changes: Readonly<Record<string, string | null>>) => navigate(logHref(params, changes), { preventScrollReset: true });
  const { source, action, target = "", actor } = view.filters;
  const who = facetsWith(WHO_FACETS, source), what = facetsWith(WHAT_FACETS, action);
  const filtered = LOG_FILTERS.some((name) => view.filters[name] !== undefined) || DATE_FILTERS.some((name) => view.dates[name] !== undefined);
  const clearHref = logHref(params, Object.fromEntries([...LOG_FILTERS, ...DATE_FILTERS].map((name) => [name, null])));
  // A line target reads by its name in the field and goes to the API by its id (#1341).
  const [targetField, setTargetField] = useUrlField(lineById(target) ? lineDisplayName(target) : target);
  const [fromField, setFromField] = useUrlField(view.dates.from ?? "");
  const [toField, setToField] = useUrlField(view.dates.to ?? "");
  const applyTarget = (value: string) => {
    const key = lineByName(value)?.id ?? value.trim();
    if (key !== target) go({ target: key || null });
  };
  const onTargetKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    applyTarget(event.currentTarget.value);
  };
  const onDate = (name: "from" | "to", field: string, setField: (next: string) => void) => {
    const current = view.dates[name];
    // A whole day applies; an empty field clears the filter; anything else goes back to the URL's day.
    const settle = (value: string) => {
      if (!value) { if (current) go({ [name]: null }); return; }
      if (meantDay(value)) { if (value !== current) go({ [name]: value }); return; }
      setField(current ?? "");
    };
    return {
      value: field,
      onChange: (event: { currentTarget: HTMLInputElement }) => {
        const value = event.currentTarget.value;
        setField(value);
        if (meantDay(value) && value !== current) go({ [name]: value });
      },
      onBlur: (event: FocusEvent<HTMLInputElement>) => settle(event.currentTarget.value),
      onKeyDown: (event: KeyboardEvent<HTMLInputElement>) => { if (event.key === "Enter") { event.preventDefault(); settle(event.currentTarget.value); } },
    };
  };
  return <form className="page-toolbar act-filters" role="search" aria-label="Activity log filters" onSubmit={(event) => event.preventDefault()}>
    <div className="act-facets">
      <div className="act-facet"><span className="act-facet-label" aria-hidden="true">Who</span><Chips kind="filter" small label="Who" items={facetChips(params, "source", who, source)} /></div>
      <div className="act-facet"><span className="act-facet-label" aria-hidden="true">What</span><Chips kind="filter" small label="What" items={facetChips(params, "action", what, action)} /></div>
    </div>
    <div className="act-fields">
      <label className="act-select">Who<select name="source" value={source ?? ""} onChange={(event) => go({ source: event.target.value || null })}>
        <option value="">All</option>
        {who.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <label className="act-select">What<select name="action" value={action ?? ""} onChange={(event) => go({ action: event.target.value || null })}>
        <option value="">All</option>
        {what.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <label className="act-target">Target<input name="target" type="search" value={targetField} onChange={(event) => setTargetField(event.currentTarget.value)} autoComplete="off" placeholder="Wall drip or mister_engage_kpa"
        onBlur={(event) => applyTarget(event.currentTarget.value)} onKeyDown={onTargetKey} /></label>
      <label className="act-date">From<input name="from" type="date" max={view.dates.to} {...onDate("from", fromField, setFromField)} /></label>
      <label className="act-date">To<input name="to" type="date" min={view.dates.from} {...onDate("to", toField, setToField)} /></label>
      {actor && <Link className="act-clear-chip" to={logHref(params, { actor: null })} preventScrollReset>Only {personName(actor, me)}<span aria-hidden="true"> ×</span><span className="screen-reader">, show everyone</span></Link>}
      {filtered && <Link className="act-clear" to={clearHref} preventScrollReset>Clear filters</Link>}
    </div>
  </form>;
}
