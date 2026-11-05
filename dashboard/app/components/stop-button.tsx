// SPDX-License-Identifier: MIT
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { fetchRead } from "../lib/queries";
import { OperatorOnly } from "../lib/session";
import { READBACK_POLL_MS, useStop } from "../lib/writes";
import { Button } from "./button";

/**
 * A line's own Stop (SCR-04, US-WAT-13, redesign D-UX-06): API-IRR-07 `{line_id}` through #1125's `useStop`, one tap and
 * no dialog, for operators, on every line but fog in every state. It is filled red (`.btn-stop`) only while that line's
 * relay is on or unknown (`filled`), and a quiet outline otherwise; either way it is never disabled by the writes flag,
 * and renders the same before the irrigation firmware, when the bridge turns the open wet relays off. `useStop` then
 * gives the one Stop result toast (what can still start, and Disable for a line that was running). While it confirms,
 * the shared API-IRR-01 entry is read every 5 s, so the row's relay edge shows without waiting for the 15 s poll.
 */
export function StopButton({ lineId, filled = true }: { lineId: string; filled?: boolean }) {
  const { stop, sending, confirming } = useStop();
  const client = useQueryClient();
  useEffect(() => {
    if (!confirming) return;
    const timer = setInterval(() => { void fetchRead(client, "API-IRR-01", {}).catch(() => {}); }, READBACK_POLL_MS);
    return () => clearInterval(timer);
  }, [client, confirming]);
  const label = <>Stop <span className="screen-reader">{lineId}</span></>;
  return <>
    {filled
      ? <Button variant="stop" write busy={sending} onClick={() => void stop(lineId)}>{label}</Button>
      // The quiet Stop: an outline that the writes flag never disables, as `.btn-stop` is not (design system §4.5).
      : <OperatorOnly><button type="button" className="btn btn-stop-quiet" data-write-control="true" disabled={sending} onClick={() => void stop(lineId)}>{sending ? "Sending…" : label}</button></OperatorOnly>}
    {confirming && <OperatorOnly><span className="state-note" data-stop-note={lineId}>Waiting for the valve to read off</span></OperatorOnly>}
  </>;
}
