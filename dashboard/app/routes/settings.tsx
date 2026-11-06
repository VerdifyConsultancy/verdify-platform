// SPDX-License-Identifier: MIT
import { Link, useLocation, useSearchParams } from "react-router";
import { chartLinks } from "../charts/uplot-chart";
import { SettingsSearch } from "../components/control/find-setting";
import { isManual } from "../components/control/marks";
import { SettingsCatalog, groupRows } from "../components/control/settings-catalog";
import { SettingDrawerHost, useReadbackStale, useSettingEdit, withParams } from "../components/control/setting-drawer";
import { SettingsRail, type RailFilter, type RailGroup } from "../components/control/settings-rail";
import { AgeLabel, useClockValue } from "../components/freshness";
import { PageHeader } from "../components/layout/page-header";
import { SETTING_GROUPS, changedByAgent, groupLabel, recentlyChanged, useToday } from "../components/setting-row";
import type { Setting } from "../lib/operator-api";
import { useSettingsRead } from "../lib/queries";
import { viewMeta } from "../lib/sections";
import { useShellNow } from "../lib/shell";
import { Panel } from "../components/panel";
import { GlossaryList } from "../components/term";
import { GLOSSARY } from "../lib/glossary";

export const meta = () => viewMeta("control-settings");
export const links = () => chartLinks();

/**
 * SCR-05 Settings (`/settings`, redesign §5.10): every registry row from API-09 in fixed groups. The header states the
 * catalog in one line and holds the search; the rail filters it (Show, with counts) and jumps to a group; each row is
 * the device value large with its bounds bar and one pill, over one meta line. `?edit=<param>` opens the setting drawer
 * (components/control), which saves through API-10 and releases through API-11. Search (`q`), the filter (`filter`), the
 * open drawer (`edit`) and its history window (`hist`) are in the URL. No greenhouse data renders in SSR.
 */

// ---------------------------------------------------------------------------------------------------- filters
type Filter = { readonly key: string; readonly label: string; readonly test: (row: Setting, now: number) => boolean; readonly empty: string };
/** Show, All first; each counts the rows it keeps (SCR-05, redesign §5.10). */
export const SETTING_FILTERS: readonly Filter[] = [
  { key: "all", label: "All", test: () => true, empty: "No settings yet" },
  { key: "recent", label: "Recently changed", test: (row, now) => recentlyChanged(row, now), empty: "No setting changed in the last 7 days" },
  { key: "requested", label: "Requested", test: (row) => row.state === "requested", empty: "No setting is waiting for the controller" },
  { key: "not_confirmed", label: "Not confirmed", test: (row) => row.state === "not_confirmed", empty: "No setting reads not confirmed" },
  { key: "manual", label: "Manual", test: (row) => isManual(row), empty: "No setting is manual" },
  { key: "agent", label: "Changed by an agent", test: changedByAgent, empty: "No value in force was written by Iris or an agent" },
  { key: "read_only", label: "Read-only", test: (row) => !row.writable, empty: "No read-only setting" },
];

/**
 * "Recently changed" needs the time of each write: it is left out when API-09 answers with desired rows that carry no
 * readable `created_at` (redesign §5.10). Before the first answer it stands, so the rail does not move when it loads.
 */
export function servesWriteTime(items: readonly Setting[] | null): boolean {
  if (!items) return true;
  const written = items.filter((row) => row.desired);
  return written.length === 0 || written.some((row) => Number.isFinite(Date.parse(row.desired!.created_at)));
}

/** Search matches the plain label and the key, case-insensitively. */
export function matchesSearch(row: Pick<Setting, "label" | "param">, query: string): boolean {
  const needle = query.trim().toLowerCase();
  return !needle || row.label.toLowerCase().includes(needle) || row.param.toLowerCase().includes(needle);
}

/**
 * The status line: "261 settings · 2 requested · 0 not confirmed · 5 manual"; on a phone (`short`, redesign §5.10)
 * "261 settings · 2 requested", with "1 not confirmed" after it while any is. Null until API-09 first answers.
 */
export function settingsStatus(items: readonly Setting[] | null, short = false): string | null {
  if (!items) return null;
  const count = (test: (row: Setting) => boolean) => items.filter(test).length;
  const total = `${items.length} ${items.length === 1 ? "setting" : "settings"} · ${count((row) => row.state === "requested")} requested`;
  const notConfirmed = count((row) => row.state === "not_confirmed");
  if (short) return notConfirmed > 0 ? `${total} · ${notConfirmed} not confirmed` : total;
  return `${total} · ${notConfirmed} not confirmed · ${count(isManual)} manual`;
}

/** The rail's groups, in the catalog's order, with the rows each holds under the current filter and search ("—" while loading). */
function railGroups(items: readonly Setting[] | null, shown: readonly Setting[]): RailGroup[] {
  const counts = new Map(groupRows(shown).map(([group, rows]) => [group, rows.length]));
  const known = SETTING_GROUPS.map(([group]) => group);
  const extra = [...new Set((items ?? []).map((row) => row.group))].filter((group) => !known.includes(group));
  return [...known, ...extra].map((group) => ({ group, label: groupLabel(group), count: items ? counts.get(group) ?? 0 : null }));
}

// ---------------------------------------------------------------------------------------------------- the page
export default function Settings() {
  const [params, setParams] = useSearchParams();
  const { pathname } = useLocation();
  const query = params.get("q") ?? "";
  const read = useSettingsRead();
  const items = read.data?.items ?? null;
  const filters = SETTING_FILTERS.filter((item) => item.key !== "recent" || servesWriteTime(items));
  const filter = filters.find((item) => item.key === params.get("filter")) ?? filters[0];
  // "Recently changed" counts on the server's clock, to the minute.
  const now = useClockValue((at) => Math.floor(at / 60_000)) * 60_000;
  const today = useToday();
  const readbackStale = useReadbackStale();
  const shellNow = useShellNow();
  const { editTo } = useSettingEdit();
  const shown = items ? items.filter((row) => filter.test(row, now) && matchesSearch(row, query)) : null;
  const rail: RailFilter[] = filters.map((item) => ({
    key: item.key, label: item.label, count: items ? items.filter((row) => item.test(row, now)).length : null, current: item.key === filter.key,
    to: withParams(pathname, params, { filter: item.key === "all" ? null : item.key, edit: null, hist: null }),
  }));
  const search = (text: string) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (text) next.set("q", text); else next.delete("q");
    return next;
  }, { replace: true, preventScrollReset: true });
  const searched = query.trim();
  // An empty list is one line and one action that widens it (redesign §3.6).
  const empty = searched
    ? <p><span>No setting matches “{searched}”</span> <Link className="ctl-empty-action" to={withParams(pathname, params, { q: null })}>Clear search</Link></p>
    : <p><span>{filter.empty}</span>{filter.key !== "all" && <> <Link className="ctl-empty-action" to={withParams(pathname, params, { filter: null })}>Show all</Link></>}</p>;
  return <>
    <PageHeader view="control-settings" status={settingsStatus(items)} statusShort={settingsStatus(items, true)} actions={<SettingsSearch value={query} onChange={search} />} />
    <div className="l-rail ctl-settings">
      <SettingsRail filters={rail} groups={railGroups(items, shown ?? [])} />
      <div className="ctl-settings-main">
        {readbackStale && items && <p className="notice notice-warn ctl-stale-note">
          Device values are stale: <AgeLabel observedAt={shellNow.data?.freshness.readback.observed_at} stale />
        </p>}
        <SettingsCatalog rows={shown} failed={read.isError} onRetry={() => void read.refetch()} readbackStale={readbackStale} editTo={editTo} today={today} empty={empty} />
      </div>
    </div>
    <GlossaryPanel />
    <SettingDrawerHost />
  </>;
}

/** The glossary (#1339), from the one table every page's `Term` reads, so docs and MCP can cite `/settings#glossary`. */
const GLOSSARY_ENTRIES = Object.entries(GLOSSARY).map(([id, entry]) => ({ id, ...entry }));

/**
 * The glossary folded under its count, so its definitions (which name verdicts such as "In band today") never read as
 * the page's own state while data is missing; a link to `#glossary` or one term's `#term-<id>` opens it.
 */
function GlossaryPanel() {
  const { hash } = useLocation();
  const linked = hash === "#glossary" || hash.startsWith("#term-");
  return <Panel id="glossary" title="Glossary" thing="the glossary" state="ready">
    <details className="glossary" open={linked}>
      <summary>{GLOSSARY_ENTRIES.length} terms the pages use</summary>
      <GlossaryList entries={GLOSSARY_ENTRIES} />
    </details>
  </Panel>;
}
