// SPDX-License-Identifier: MIT
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { fetchRead } from "../lib/queries";
import { READBACK_POLL_MS, useStop } from "../lib/writes";
import { Button } from "./button";

/**
 * Stop on a line card (SCR-04, US-WAT-13): API-IRR-07 `{line_id}` through #1125's `useStop`, `.btn-stop`, one tap and no
 * dialog, for operators. It is never disabled by the writes flag, and renders the same before the irrigation firmware,
 * when the bridge turns the open wet relays off. `useStop` then gives the one Stop result toast (what can still start,
 * and Disable for a line that was running). While it confirms, the shared API-IRR-01 entry is read every 5 s, so the
 * card's relay edge shows without waiting for the board's 15 s poll.
 */
export function StopButton({ lineId }: { lineId: string }) {
  const { stop, sending, confirming } = useStop();
  const client = useQueryClient();
  useEffect(() => {
    if (!confirming) return;
    const timer = setInterval(() => { void fetchRead(client, "API-IRR-01", {}).catch(() => {}); }, READBACK_POLL_MS);
    return () => clearInterval(timer);
  }, [client, confirming]);
  return <>
    <Button variant="stop" write busy={sending} onClick={() => void stop(lineId)}>Stop <span className="screen-reader">{lineId}</span></Button>
    {confirming && <span className="state-note" data-stop-note={lineId}>Waiting for the valve to read off</span>}
  </>;
}
