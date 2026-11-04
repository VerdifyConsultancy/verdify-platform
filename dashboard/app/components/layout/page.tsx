// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { cls } from "../common";

/**
 * Page (redesign §3.1): every screen's outer stack, its page header, then sections at the §3.4 gaps (`--space-6`
 * between sections on desktop, `--space-5` on phones). The page width is the shell's; a route that wants
 * `--shell-wide` says so with `export const handle = { wide: true }` (app-shell.tsx `PageHandle`).
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
