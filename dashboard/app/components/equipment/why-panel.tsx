// SPDX-License-Identifier: MIT
import { Link } from "react-router";
import { graphWindowUrl } from "../../lib/graph-link";
import type { Equipment } from "../../lib/operator-api";
import { hasWords, words } from "../../lib/words";
import { Panel, type PanelState } from "../panel";
import { Ago, Since } from "./report-times";

type ReasonKey = "mode_reason" | "fog_block_reason" | "moisture_block_reason";
type ReasonState = NonNullable<Equipment[ReasonKey]>;

/** The three rows in their fixed order. Each reason's key is its family in the words table. */
export const WHY_ROWS: readonly { readonly key: ReasonKey; readonly label: string }[] = [
  { key: "mode_reason", label: "Climate mode" },
  { key: "fog_block_reason", label: "Fog (L11)" },
  { key: "moisture_block_reason", label: "Misters (L04, L07, L09)" },
];

/** A reason's Graphs link opens this long either side of when its value began. */
export const WHY_WINDOW_MS = 2 * 3_600_000;
/** The Graphs window around `since`; null for a time that cannot be read. */
export function whyGraphUrl(since: string): string | null {
  const at = Date.parse(since);
  return Number.isFinite(at) ? graphWindowUrl(at - WHY_WINDOW_MS, at + WHY_WINDOW_MS) : null;
}

/**
 * One reason as API-13 serves it. A code reads in words, or raw in mono when the table has none (the firmware's own
 * `unknown` is a code with words), with "since HH:MM" and a link to the Graphs window where it began. A null code is the
 * API saying the controller is not reporting, or has no row: "unknown · last report 14 min ago", never its last words.
 */
function WhyRow({ family, label, reason }: { family: ReasonKey; label: string; reason: ReasonState | null }) {
  const code = reason?.code ?? null;
  const since = code === null ? null : reason?.since ?? null;
  const link = since ? whyGraphUrl(since) : null;
  return <li className="why-row" data-reason={family}>
    <span className="why-label">{label}</span>
    {code === null
      ? <span className="why-words" data-unknown="true">unknown · {reason?.observed_at ? <>last report <Ago at={reason.observed_at} /></> : "no data"}</span>
      : <span className="why-words">
        {hasWords(family, code) ? words(family, code) : <code className="why-raw">{code}</code>}
        {since && <span className="why-since"> · since <Since at={since} /></span>}
      </span>}
    {link && <Link className="why-link" to={link}>Graphs <span className="screen-reader">around when this began</span><span aria-hidden="true">›</span></Link>}
  </li>;
}

/**
 * Why (SCR-07, US-EQP-02): `mode_reason`, `fog_block_reason` and `moisture_block_reason` from the board's own API-13
 * read, so it refreshes with the board. A card under the three groups, one row per reason at every width.
 */
export function WhyPanel({ equipment, state, onRetry }: { equipment: Equipment | null | undefined; state: PanelState; onRetry?: () => void }) {
  return <Panel title="Why" thing="the reasons" state={state} reserve="sm" onRetry={onRetry}>
    <ul className="why-rows">
      {WHY_ROWS.map(({ key, label }) => <WhyRow key={key} family={key} label={label} reason={equipment?.[key] ?? null} />)}
    </ul>
  </Panel>;
}
