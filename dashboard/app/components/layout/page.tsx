// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { cls } from "../common";

/**
 * Page (redesign §3.1): every screen's outer stack, its page header, then sections at the §3.4 gaps (`--space-6`
 * between sections on desktop, `--space-5` on phones). The page width is the shell's on every route: header, main and
 * footer share one centred container (decisions log, 2026-11-04).
 */
export function Page({ children, className }: { children?: ReactNode; className?: string }) {
  return <div className={cls("page", className)}>{children}</div>;
}

/**
 * The `.l-scroll-x` region (redesign §3.2): a labelled, focusable horizontal scroll area for a wide table, the week
 * matrix or a long strip, so the page itself never scrolls sideways.
 */
export function ScrollRegion({ label, children, className }: { label: string; children?: ReactNode; className?: string }) {
  return <div className={cls("l-scroll-x", className)} role="region" tabIndex={0} aria-label={label}>{children}</div>;
}
