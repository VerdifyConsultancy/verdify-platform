// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link } from "react-router";

export type SectionLink = { readonly to: string; readonly label: string };

export type SectionHeaderProps = {
  /** The section's h2: "Watering minutes". */
  title: ReactNode;
  id?: string;
  headingLevel?: 2 | 3;
  /** A count or short meta beside the title ("· last 7 days", "14 of 24"). */
  meta?: ReactNode;
  /** Chips or a toggle for the section ([7 d] [30 d]). */
  chips?: ReactNode;
  /** The one "All … ›" link at the right. */
  link?: SectionLink;
};

/**
 * SectionHeader (redesign §3.5): the only section heading. An h2 at weather's size (never an eyebrow styled as a
 * heading), an optional count or meta, optional chips and one "All … ›" link at the right.
 */
export function SectionHeader({ title, id, headingLevel = 2, meta, chips, link }: SectionHeaderProps) {
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return <div className="section-header">
    <div className="section-header-title">
      <Heading id={id}>{title}</Heading>
      {meta && <span className="meta">{meta}</span>}
    </div>
    {chips && <div className="section-header-chips">{chips}</div>}
    {link && <Link className="section-link" to={link.to}>{link.label}<span aria-hidden="true"> ›</span></Link>}
  </div>;
}

/** A section of a page (redesign §3.4): its header, then its content, at the section gaps of `Page`. */
export function Section({ header, children, className }: { header?: ReactNode; children?: ReactNode; className?: string }) {
  return <section className={className ? `page-section ${className}` : "page-section"}>{header}{children}</section>;
}
