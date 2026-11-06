// SPDX-License-Identifier: MIT
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { lineDisplayName } from "../lib/lines";
import { fetchRead } from "../lib/queries";
import { OperatorOnly } from "../lib/session";
import { READBACK_POLL_MS, useStop } from "../lib/writes";
import { Button } from "./button";

/**
 * A line's own Stop (SCR-04, US-WAT-13, redesign D-UX-06): API-IRR-07 `{line_id}` through #1125's `useStop`, one tap and
 * no dialog, for operators. The row shows it only while that line runs or its relay reads unknown (D-UX-06 as Jason set
 * it on 2026-11-06, #1349); it is filled red (`.btn-stop`), never disabled by the writes flag, and renders the same
 * before the irrigation firmware, when the bridge turns the open wet relays off. `useStop` then
 * gives the one Stop result toast (what can still start, and Disable for a line that was running). While it confirms,
 * the shared API-IRR-01 entry is read every 5 s, so the row's relay edge shows without waiting for the 15 s poll.
 */
export function StopButton({ lineId }: { lineId: string }) {
  const { stop, sending, confirming } = useStop();
  const client = useQueryClient();
  useEffect(() => {
    if (!confirming) return;
    const timer = setInterval(() => { void fetchRead(client, "API-IRR-01", {}).catch(() => {}); }, READBACK_POLL_MS);
    return () => clearInterval(timer);
  }, [client, confirming]);
  const label = <>Stop <span className="screen-reader">{lineDisplayName(lineId)}</span></>;
  return <>
    <Button variant="stop" write busy={sending} onClick={() => void stop(lineId)}>{label}</Button>
    {confirming && <OperatorOnly><span className="state-note" data-stop-note={lineId}>Waiting for the valve to read off</span></OperatorOnly>}
  </>;
}
