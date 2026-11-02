// SPDX-License-Identifier: MIT
import type { ReactNode, SyntheticEvent } from "react";

/** Progressive detail: a native `<details>` whose `<summary>` is a 44 px target (weather's `summary` rule). */
export function Details({ summary, children, open, onToggle, className, id }: { summary: ReactNode; children?: ReactNode; open?: boolean; onToggle?: (event: SyntheticEvent<HTMLDetailsElement>) => void; className?: string; id?: string }) {
  return <details id={id} className={className} open={open} onToggle={onToggle}><summary>{summary}</summary>{children}</details>;
}
