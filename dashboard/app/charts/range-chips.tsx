// SPDX-License-Identifier: MIT
import { Link } from "react-router";

/**
 * Range chips 24 h, 72 h, 7 d and 30 d (design system §5.3) as links with `aria-current`, on weather's `.chips` classes
 * (#1123's `Chips` replaces the markup later). The caller builds each href; Graphs keeps the range in the URL (#1130).
 */
export const RANGES = [
  { id: "24h", label: "24 h", seconds: 86_400 },
  { id: "72h", label: "72 h", seconds: 3 * 86_400 },
  { id: "7d", label: "7 d", seconds: 7 * 86_400 },
  { id: "30d", label: "30 d", seconds: 30 * 86_400 },
] as const;
export type RangeId = (typeof RANGES)[number]["id"];

export function RangeChips({ current, href }: { current: string | null; href: (range: RangeId) => string }) {
  return <ul className="chips chips-sm range-chips" aria-label="Range">
    {RANGES.map((range) => <li key={range.id}>
      <Link to={href(range.id)} aria-current={range.id === current ? "page" : undefined} preventScrollReset>{range.label}</Link>
    </li>)}
  </ul>;
}
