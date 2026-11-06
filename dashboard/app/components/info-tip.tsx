// SPDX-License-Identifier: MIT
import { useState, type ReactNode } from "react";
import { Popover } from "./popover";

/**
 * A term's explanation (#1344, #1349): a small "i" button beside it that opens its words in a popover (a bottom sheet on a
 * phone). The button's name is "About <term>", so a screen reader finds it; the words are never only in a tooltip.
 */
export function InfoTip({ term, children }: { term: string; children: ReactNode }) {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  return <>
    <button type="button" className="info-tip" aria-haspopup="dialog" aria-expanded={anchor !== null} aria-label={`About ${term}`}
      onClick={(event) => setAnchor(anchor ? null : event.currentTarget)} />
    <Popover open={anchor !== null} anchor={anchor} onClose={() => setAnchor(null)} title={term} className="info-tip-pop">{children}</Popover>
  </>;
}
