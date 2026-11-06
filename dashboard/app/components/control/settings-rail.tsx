// SPDX-License-Identifier: MIT
import { useId, type MouseEvent, type ReactNode } from "react";
import { Link, useNavigate } from "react-router";
import { cls } from "../common";

/**
 * Settings' rail (redesign §5.10): Show (the filters, each with its count as a pill, kept in the URL as `filter=`) and
 * Groups (anchors to each group of the catalog, with the rows each holds now as a plain muted number), so the two read as
 * the different things they are. From 861 px it is a sticky column beside the catalog; at 860 px and below the same
 * choices are two selects above it, Show and "Jump to group". Both render and the stylesheet shows one, so the server's
 * HTML is the same at every width.
 */
export type RailFilter = {
  readonly key: string; readonly label: string; readonly count: number | null; readonly to: string; readonly current: boolean;
  /** What the filter keeps, in one line (#1333): the item's title, and the legend's entry. */
  readonly definition?: string;
};
/** A group and the rows it holds under the current filter and search; null before API-09 first answers. */
export type RailGroup = { readonly group: string; readonly label: string; readonly count: number | null };

/** The id of a group's head in the catalog, the anchor the rail jumps to. */
export const groupAnchor = (group: string): string => `group-${group}`;

/** Scrolls the catalog to a group's head and moves focus there, without a history entry. */
function jumpTo(group: string): boolean {
  const head = typeof document === "undefined" ? null : document.getElementById(groupAnchor(group));
  if (!head) return false;
  // jsdom has no scrollIntoView; a browser always does.
  head.scrollIntoView?.({ block: "start" });
  head.focus({ preventScroll: true });
  return true;
}

const countText = (count: number | null) => (count === null ? "—" : String(count));

/**
 * `foot` renders under both the lists and the selects, at every width: the window switch, the retired toggle and the
 * legend of definitions (#1333).
 */
export function SettingsRail({ filters, groups, foot }: { filters: readonly RailFilter[]; groups: readonly RailGroup[]; foot?: ReactNode }) {
  const showId = useId(), groupsId = useId(), showSelect = useId(), groupSelect = useId();
  const navigate = useNavigate();
  const current = filters.find((item) => item.current) ?? filters[0];
  const jump = (group: string) => (event: MouseEvent<HTMLAnchorElement>) => { if (jumpTo(group)) event.preventDefault(); };
  return <nav className="ctl-rail" aria-label="Settings filters and groups">
    <div className="ctl-rail-lists">
      <p id={showId} className="eyebrow ctl-rail-label">Show</p>
      <ul className="ctl-rail-list ctl-rail-filters" aria-labelledby={showId}>
        {filters.map((item) => <li key={item.key}>
          <Link className="ctl-rail-item" to={item.to} aria-current={item.current ? "true" : undefined} title={item.definition}>
            <span className="ctl-rail-name">{item.label}</span> <span className="ctl-rail-count">{countText(item.count)}</span>
          </Link>
        </li>)}
      </ul>
      <p id={groupsId} className="eyebrow ctl-rail-label">Groups</p>
      <ul className="ctl-rail-list ctl-rail-groups" aria-labelledby={groupsId}>
        {groups.map((item) => <li key={item.group}>
          {item.count !== null && item.count > 0
            ? <a className="ctl-rail-item" href={`#${groupAnchor(item.group)}`} onClick={jump(item.group)}>
              <span className="ctl-rail-name">{item.label}</span> <span className="ctl-rail-count">{item.count}</span>
            </a>
            : <span className={cls("ctl-rail-item", "is-empty")}><span className="ctl-rail-name">{item.label}</span> <span className="ctl-rail-count">{countText(item.count)}</span></span>}
        </li>)}
      </ul>
    </div>
    <div className="ctl-rail-selects">
      <div className="field">
        <label htmlFor={showSelect}>Show</label>
        <select id={showSelect} value={current?.key ?? ""} onChange={(event) => {
          const chosen = filters.find((item) => item.key === event.target.value);
          if (chosen) navigate(chosen.to);
        }}>
          {filters.map((item) => <option key={item.key} value={item.key}>{item.label} ({countText(item.count)})</option>)}
        </select>
      </div>
      <div className="field">
        <label htmlFor={groupSelect}>Group</label>
        <select id={groupSelect} value="" onChange={(event) => { if (event.target.value) jumpTo(event.target.value); }}
          disabled={!groups.some((item) => item.count !== null && item.count > 0)}>
          <option value="">Jump to…</option>
          {groups.filter((item) => item.count !== null && item.count > 0).map((item) => <option key={item.group} value={item.group}>{item.label} ({item.count})</option>)}
        </select>
      </div>
    </div>
    {foot && <div className="ctl-rail-foot">{foot}</div>}
  </nav>;
}
