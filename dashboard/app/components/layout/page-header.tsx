// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link } from "react-router";
import { sectionOf, viewOf, type ViewId } from "../../lib/sections";
import { Chips } from "../chips";
import { cls } from "../common";

/**
 * A section's tabs (redesign §2.4): a labelled `nav` ("Water views") of nav chips, the current one
 * `aria-current="page"` and filled ink. A tab is a link, so a tab change adds to history and Back returns to the
 * previous tab. A section with one view has none.
 */
export function SectionTabs({ view }: { view: ViewId }) {
  const current = viewOf(view);
  const section = sectionOf(current.section);
  if (section.views.length < 2) return null;
  return <nav className="section-tabs" aria-label={`${section.label} views`}>
    <Chips kind="nav" items={section.views.map((item) => ({ key: item.id, label: item.tab, to: item.path, current: item.id === view }))} />
  </nav>;
}

export type PageHeaderProps = {
  /** The view this page is (lib/sections.ts): its section names the eyebrow, its title the h1. */
  view: ViewId;
  /**
   * The h1 when the page shows a focused state of its view ("South zone" on Zones). The eyebrow then reads section ›
   * view, both links ("GRAPHS › ZONES").
   */
  title?: string;
  /**
   * The status line: the page's one sentence of state, from data, in `.meta` (redesign §3.5). Pass null while it loads:
   * the line keeps its height, so nothing shifts when it fills. Leave it out for a page with none.
   */
  status?: ReactNode;
  /** At most two buttons, the primary last: at the h1 row's right on desktop, under the status line on phones. */
  actions?: ReactNode;
  /** At the end of the tabs row: Water's Stop all water, which shares the sticky row on phones. */
  tabsEnd?: ReactNode;
  /** The tabs row sticks under the header at 860 px and below (Water, redesign §2.5). */
  stickyTabs?: boolean;
};

/**
 * Every screen's head (redesign §3.1, §3.5): the eyebrow, which is the breadcrumb (the section's name, a link to its
 * default view except on that view), the one h1, the status line and the actions, then the section's tabs. The eyebrow
 * labels; it is never a heading. Now has no page header: its hero is its head.
 */
export function PageHeader({ view, title, status, actions, tabsEnd, stickyTabs = false }: PageHeaderProps) {
  const current = viewOf(view);
  const section = sectionOf(current.section);
  const focused = title !== undefined && title !== current.title;
  const onDefault = section.views[0].id === view && !focused;
  return <>
    <div className="page-header">
      <p className="eyebrow page-crumbs">
        {onDefault ? <span>{section.label}</span> : <Link to={section.to}>{section.label}</Link>}
        {focused && <><span className="page-crumb-sep" aria-hidden="true"> › </span><Link to={current.path}>{current.tab}</Link></>}
      </p>
      <h1>{title ?? current.title}</h1>
      {status !== undefined && <p className="page-status meta">{status}</p>}
      {actions && <div className="page-actions">{actions}</div>}
    </div>
    {section.views.length > 1 && <div className={cls("page-tabs", stickyTabs && "page-tabs-sticky")}>
      <SectionTabs view={view} />
      {tabsEnd && <div className="page-tabs-end">{tabsEnd}</div>}
    </div>}
  </>;
}
