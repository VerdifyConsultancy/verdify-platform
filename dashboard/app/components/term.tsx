// SPDX-License-Identifier: MIT
/**
 * A term with its definition one tap away (#1339): the words stay visible, dotted underneath, and a tap or Enter opens
 * the glossary entry (`lib/glossary.ts`) in a `Popover`, under the term from 861 px and as a bottom sheet on a phone.
 * Never hover-only: a phone has no hover. The popover links to the whole table at Control → Settings.
 */
import { useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { GLOSSARY_PATH, glossaryEntry, type GlossaryId } from "../lib/glossary";
import { cls } from "./common";
import { Popover } from "./popover";

export type TermProps = {
  readonly id: GlossaryId;
  /** The words shown; the entry's own term when absent. */
  readonly children?: ReactNode;
  readonly className?: string;
};

export function Term({ id, children, className }: TermProps) {
  const entry = glossaryEntry(id);
  const ref = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  return <>
    <button ref={ref} type="button" className={cls("term", className)} data-term={id} aria-haspopup="dialog" aria-expanded={open}
      onClick={() => setOpen((value) => !value)}>
      {children ?? entry.term}
    </button>
    <Popover open={open} onClose={() => setOpen(false)} anchor={ref.current} title={entry.term} className="term-popover">
      <p className="term-definition">{entry.definition}</p>
      <p className="meta"><Link to={GLOSSARY_PATH} onClick={() => setOpen(false)}>All terms<span aria-hidden="true">{" ›"}</span></Link></p>
    </Popover>
  </>;
}

/** The whole table as a definition list, for Control → Settings' "Words on the pages" (`#glossary`). */
export function GlossaryList({ entries }: { entries: readonly { readonly id: string; readonly term: string; readonly definition: string }[] }) {
  return <dl className="glossary-list">
    {entries.map((entry) => <div key={entry.id} className="glossary-item" id={`term-${entry.id}`}>
      <dt>{entry.term}</dt>
      <dd>{entry.definition}</dd>
    </div>)}
  </dl>;
}
