// SPDX-License-Identifier: MIT
import { useState } from "react";
import type { Alert } from "../../lib/operator-api";
import { useRecordWrite } from "../../lib/writes";
import { words } from "../../lib/words";
import { Button } from "../button";
import { Details } from "../details";
import { Reason } from "../forms/reason";

/**
 * Acknowledge an alert in one tap, with an optional note (at most 500 characters) in an "Add a note" disclosure beside it
 * (redesign §5.8): one API-15 request with one `Idempotency-Key` (#1125's R hook), whose answer `onAcknowledged` gets.
 * The same control on Now's Needs someone and on an Activity row. Operators only (`Button write`); with changes
 * switched off it is disabled.
 */
export function Acknowledge({ alert, onAcknowledged }: { alert: Alert; onAcknowledged?: (alert: Alert) => void }) {
  const record = useRecordWrite();
  const [note, setNote] = useState("");
  const title = words("alert_type", alert.alert_type);
  const send = async () => {
    const outcome = await record.acknowledge(alert.id, note.trim() || null);
    if (!outcome.ok) return;
    setNote("");
    onAcknowledged?.(outcome.data);
  };
  return <>
    <Button write wide busy={record.sending} onClick={() => void send()}>Acknowledge <span className="screen-reader">{title}</span></Button>
    <Details summary="Add a note" className="ack-note">
      <Reason value={note} onChange={setNote} label="Note (optional)" error={record.error?.field === "note" ? record.error.message : null} />
    </Details>
    {record.error && record.error.field !== "note" && <p className="field-error" role="alert">{record.error.message}</p>}
  </>;
}
