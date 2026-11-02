// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link } from "react-router";

/**
 * One line under the header, in the fault or warn tint, linking to the page that explains it (screens §1.2). The
 * shell (#1124) decides which banner shows; this only draws one.
 */
export function Banner({ tone, children, link }: { tone: "fault" | "warn"; children: ReactNode; link?: { to: string; label: ReactNode } }) {
  return <p className={`notice notice-${tone} banner`} data-banner={tone}>
    <span>{children}</span>
    {link && <Link to={link.to}>{link.label}<span aria-hidden="true"> ›</span></Link>}
  </p>;
}
