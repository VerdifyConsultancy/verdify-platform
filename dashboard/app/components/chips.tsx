// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link } from "react-router";

/** A chip that navigates: the current one carries `aria-current`. `keepScroll` holds the page where it is, for chips below the fold. */
export type ChipLink = { key: string; label: ReactNode; to: string; current?: boolean; keepScroll?: boolean };
/** A chip that toggles in place: `aria-pressed` says whether it is on. */
export type ChipToggle = { key: string; label: ReactNode; pressed: boolean; onToggle: () => void; to?: undefined };

/** Ranges, metrics and filters as weather's `.chips`; the current or pressed chip is filled ink. */
export function Chips({ label, items, small = false }: { label: string; items: readonly (ChipLink | ChipToggle)[]; small?: boolean }) {
  return <ul className={small ? "chips chips-sm" : "chips"} aria-label={label}>{items.map((item) => <li key={item.key}>
    {item.to !== undefined
      ? <Link to={item.to} aria-current={item.current ? "true" : undefined} preventScrollReset={item.keepScroll}>{item.label}</Link>
      : <button type="button" className="chip" aria-pressed={item.pressed} onClick={item.onToggle}>{item.label}</button>}
  </li>)}</ul>;
}
