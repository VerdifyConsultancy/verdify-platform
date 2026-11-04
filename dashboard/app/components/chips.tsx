// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link } from "react-router";

/** A chip that navigates: the current one carries `aria-current`; `keepScroll` keeps the page where it is, for a chip that changes a view in place or sits below the fold. */
export type ChipLink = { key: string; label: ReactNode; to: string; current?: boolean; keepScroll?: boolean };
/** A chip that toggles in place: `aria-pressed` says whether it is on. */
export type ChipToggle = { key: string; label: ReactNode; pressed: boolean; onToggle: () => void; to?: undefined };

/**
 * What a row of chips is (redesign §8): `nav` chips are a page's tabs or ranges, links whose current one is
 * `aria-current="page"`; `filter` chips narrow what a page lists, links whose current one is `aria-current="true"`;
 * `toggle` chips switch something on the page, buttons with `aria-pressed`. A row may mix links and toggles.
 */
export type ChipsKind = "nav" | "filter" | "toggle";

/**
 * Ranges, metrics, filters and tabs as weather's `.chips`, the one chip row (redesign §4): the current or pressed
 * chip is filled ink. `label` names the list; a row inside a labelled `nav` may leave it out.
 */
export function Chips({ label, items, small = false, kind = "filter" }: { label?: string; items: readonly (ChipLink | ChipToggle)[]; small?: boolean; kind?: ChipsKind }) {
  const current = kind === "nav" ? "page" : "true";
  return <ul className={small ? "chips chips-sm" : "chips"} aria-label={label}>{items.map((item) => <li key={item.key}>
    {item.to !== undefined
      ? <Link to={item.to} aria-current={item.current ? current : undefined} preventScrollReset={item.keepScroll}>{item.label}</Link>
      : <button type="button" className="chip" aria-pressed={item.pressed} onClick={item.onToggle}>{item.label}</button>}
  </li>)}</ul>;
}
