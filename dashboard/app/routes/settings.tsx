// SPDX-License-Identifier: MIT
import { Link, useLocation, useSearchParams } from "react-router";
import { chartLinks } from "../charts/uplot-chart";
import { SettingsSearch } from "../components/control/find-setting";
import { isManual } from "../components/control/marks";
import { SettingsCatalog, groupRows } from "../components/control/settings-catalog";
import { SettingDrawerHost, useReadbackStale, useSettingEdit, withParams } from "../components/control/setting-drawer";
import { STATE_WORDS, SettingsLegend } from "../components/control/setting-help";
import { SettingsGlossary } from "../components/control/settings-glossary";
import { SettingsRail, type RailFilter, type RailGroup } from "../components/control/settings-rail";
import { SoilTargetsPanel } from "../components/control/soil-targets";
import { AgeLabel, useClockValue } from "../components/freshness";
import { PageHeader } from "../components/layout/page-header";
import { RECENT_WINDOWS, SETTING_GROUPS, changedByAgent, groupLabel, recentlyChanged, useToday, type RecentWindow } from "../components/setting-row";
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
 * the device value large with its bounds bar and one pill, over one meta line and the setting's one-line summary.
 * `?edit=<param>` opens the setting drawer (components/control), which saves through API-10 and releases through API-11.
 * Search (`q`), the filter (`filter`), its window (`window=7d`), retired rows (`retired=1`), the glossary (`view=glossary`,
 * every setting with its full text, #1332), the open drawer (`edit`) and its history window (`hist`) are in the URL. The
 * rail's foot defines every filter, state and read-only kind (#1333). No greenhouse data renders in SSR.
 */

// ---------------------------------------------------------------------------------------------------- filters
type Filter = {
  readonly key: string; readonly label: string; readonly definition: string; readonly test: (row: Setting, now: number) => boolean; readonly empty: string;
};
/**
 * Show, All first; each counts the rows it keeps (SCR-05, redesign §5.10) and says what it keeps (#1333). "Changed in
 * the last …" carries its window (24 h, or 7 d by `?window=7d`) and counts any write in force, Iris plans included.
 */
export function settingFilters(window: RecentWindow = "24h"): readonly Filter[] {
  const span = RECENT_WINDOWS[window];
  return [
    { key: "all", label: "All", definition: "Every setting, retired ones only while shown.", test: () => true, empty: "No settings yet" },
    {
      key: "recent", label: `Changed in the last ${span.label}`,
      definition: `A value in force written in the last ${span.label}: by a person, an agent or an Iris plan.`,
      test: (row, now) => recentlyChanged(row, now, span.ms), empty: `No setting changed in the last ${span.label}`,
    },
    { key: "requested", label: STATE_WORDS.requested.label, definition: STATE_WORDS.requested.definition, test: (row) => row.state === "requested", empty: "No setting is waiting for the controller" },
    { key: "not_confirmed", label: STATE_WORDS.not_confirmed.label, definition: STATE_WORDS.not_confirmed.definition, test: (row) => row.state === "not_confirmed", empty: "No setting reads not applied" },
    { key: "manual", label: STATE_WORDS.manual.label, definition: STATE_WORDS.manual.definition, test: (row) => isManual(row), empty: "No setting is manual" },
    {
      key: "agent", label: "Set by Iris or an agent", definition: "The value in force came from an Iris plan, or from a write by a named agent.",
      test: changedByAgent, empty: "No value in force was written by Iris or an agent",
    },
    {
      key: "read_only", label: "Read-only", definition: "Can't be written here: computed, retired, reported by the controller, or a legacy limit. Each row says which.",
      test: (row) => !row.writable, empty: "No read-only setting",
    },
  ];
}
/** The filters at the default window. */
export const SETTING_FILTERS = settingFilters();

/** A retired row: hidden unless `?retired=1` or a search is on (#1333). */
export const isRetired = (row: Pick<Setting, "control_class">): boolean => row.control_class === "retired";

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
 * The status line: "261 settings · 0 waiting · 0 not applied · 3 manual" (#1333); on a phone (`short`, redesign §5.10)
 * "261 settings · 0 waiting", with "1 not applied" after it while any is. Null until API-09 first answers.
 */
export function settingsStatus(items: readonly Setting[] | null, short = false): string | null {
  if (!items) return null;
  const count = (test: (row: Setting) => boolean) => items.filter(test).length;
  const total = `${items.length} ${items.length === 1 ? "setting" : "settings"} · ${count((row) => row.state === "requested")} waiting`;
  const notApplied = count((row) => row.state === "not_confirmed");
  if (short) return notApplied > 0 ? `${total} · ${notApplied} not applied` : total;
  return `${total} · ${notApplied} not applied · ${count(isManual)} manual`;
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
  const window: RecentWindow = params.get("window") === "7d" ? "7d" : "24h";
  const showRetired = params.get("retired") === "1";
  const glossary = params.get("view") === "glossary";
  const filters = settingFilters(window).filter((item) => item.key !== "recent" || servesWriteTime(items));
  const filter = filters.find((item) => item.key === params.get("filter")) ?? filters[0];
  // "Changed in the last …" counts on the server's clock, to the minute.
  const now = useClockValue((at) => Math.floor(at / 60_000)) * 60_000;
  const today = useToday();
  const readbackStale = useReadbackStale();
  const shellNow = useShellNow();
  const { editTo } = useSettingEdit();
  const searched = query.trim();
  // Retired rows stay out of the counts and the catalog unless shown, or a search asks for them by name.
  const pool = items ? items.filter((row) => showRetired || searched !== "" || !isRetired(row)) : null;
  const retiredCount = items ? items.filter(isRetired).length : 0;
  const shown = pool ? pool.filter((row) => filter.test(row, now) && matchesSearch(row, query)) : null;
  const rail: RailFilter[] = filters.map((item) => ({
    key: item.key, label: item.label, definition: item.definition, count: pool ? pool.filter((row) => item.test(row, now)).length : null, current: item.key === filter.key,
    to: withParams(pathname, params, { filter: item.key === "all" ? null : item.key, edit: null, hist: null }),
  }));
  const search = (text: string) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (text) next.set("q", text); else next.delete("q");
    return next;
  }, { replace: true, preventScrollReset: true });
  const link = (changes: Record<string, string | null>) => withParams(pathname, params, { ...changes, edit: null, hist: null });
  // An empty list is one line and one action that widens it (redesign §3.6).
  const empty = searched
    ? <p><span>No setting matches “{searched}”</span> <Link className="ctl-empty-action" to={withParams(pathname, params, { q: null })}>Clear search</Link></p>
    : <p><span>{filter.empty}</span>{filter.key !== "all" && <> <Link className="ctl-empty-action" to={withParams(pathname, params, { filter: null })}>Show all</Link></>}</p>;
  const foot = <>
    {filters.some((item) => item.key === "recent") && <p className="ctl-rail-window" role="group" aria-label="Changed in the last">
      <span className="ctl-rail-window-label">Changed window</span>{" "}
      {(Object.keys(RECENT_WINDOWS) as RecentWindow[]).map((key) => <Link key={key} className="chip" to={link({ window: key === "24h" ? null : key })}
        aria-current={key === window ? "true" : undefined}>{RECENT_WINDOWS[key].label}</Link>)}
    </p>}
    {retiredCount > 0 && <p className="ctl-rail-toggle">
      <Link to={link({ retired: showRetired ? null : "1" })}>{showRetired ? "Hide retired" : `Show retired (${retiredCount})`}</Link>
    </p>}
    <p className="ctl-rail-toggle">
      <Link to={link({ view: glossary ? null : "glossary" })}>{glossary ? "Back to the settings" : "Glossary: every setting explained"}</Link>
    </p>
    <SettingsLegend filters={filters} />
  </>;
  const labelOf = (param: string) => items?.find((row) => row.param === param)?.label ?? param;
  return <>
    <PageHeader view="control-settings" status={settingsStatus(items)} statusShort={settingsStatus(items, true)} actions={<SettingsSearch value={query} onChange={search} />} />
    <div className="l-rail ctl-settings">
      <SettingsRail filters={rail} groups={railGroups(pool, shown ?? [])} foot={foot} />
      <div className="ctl-settings-main">
        {readbackStale && items && <p className="notice notice-warn ctl-stale-note">
          Device values are stale: <AgeLabel observedAt={shellNow.data?.freshness.readback.observed_at} stale />
        </p>}
        {filter.key !== "all" && <p className="ctl-filter-note meta"><strong>{filter.label}:</strong> {filter.definition}</p>}
        {glossary
          ? <SettingsGlossary rows={shown} failed={read.isError} onRetry={() => void read.refetch()} editTo={editTo} labelOf={labelOf} empty={empty} />
          : <SettingsCatalog rows={shown} failed={read.isError} onRetry={() => void read.refetch()} readbackStale={readbackStale} editTo={editTo} today={today} empty={empty} />}
      </div>
    </div>
    <SoilTargetsPanel />
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
  return <Panel id="glossary" title="Words on the pages" thing="the words on the pages" state="ready">
    <details className="glossary" open={linked}>
      <summary>{GLOSSARY_ENTRIES.length} terms the pages use</summary>
      <GlossaryList entries={GLOSSARY_ENTRIES} />
    </details>
  </Panel>;
}
