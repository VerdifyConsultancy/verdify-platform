// SPDX-License-Identifier: MIT
import { useState } from "react";
import { checkedLabel } from "../lib/line-checks";
import { lineDisplayName } from "../lib/lines";
import type { IrrigationLine } from "../lib/operator-api";
import { useRole } from "../lib/session";
import { boardLines, useCheckThenRun, useWaterDrawer } from "../lib/water";
import { MANUAL_DEFAULT, errorFor, formError, useProgramWrite } from "../lib/writes";
import { EditDrawer, FieldError, ManualToggle, Reason } from "./forms";
import { useToast } from "./toast";

/** What ticking Checked goes with: enabling the line, or turning its Schedule on (D-IRP-07, D-IRP-08). */
export type CheckAction = "enable" | "schedule";

/**
 * What `?check=<line_id>` asks for, or null when there is nothing to ask: a line with its check, or one whose switches
 * ask for none. A disabled line asks to enable, unless `for=schedule` says its Schedule was pressed; an enabled mister
 * zone with Schedule off asks to turn Schedule on. An enabled drip with no check (L01) records it with its first
 * schedule in the editor (#1137), not here. `for=run` (a disabled line's Run… or Mist now…, #1343) asks to enable any
 * disabled line that is not reserved, with or without a recorded check; the run dialog follows.
 */
export function checkAction(line: Pick<IrrigationLine, "check" | "enabled" | "kind" | "schedule_member" | "reserved">, wanted: string | null): CheckAction | null {
  if (wanted === "run") return !line.enabled && !line.reserved ? "enable" : null;
  if (line.check) return null;
  const canSchedule = line.kind === "mister" && line.schedule_member !== true;
  if (wanted === "schedule") return canSchedule ? "schedule" : null;
  if (!line.enabled) return "enable";
  return canSchedule ? "schedule" : null;
}

const WORDS: Readonly<Record<CheckAction, { readonly title: string; readonly save: string; readonly done: string }>> = {
  enable: { title: "Enable", save: "Enable", done: "enabled" },
  schedule: { title: "Turn on Schedule for", save: "Turn on Schedule", done: "Schedule on" },
};

/** What the sheet says above its fields when a disabled line's Run… opened it (#1343). */
export const RUN_THEN_TEXT = "This line is off. Enable it, then the run dialog opens.";

function CheckForm({ line, action, forRun, onClose }: { line: IrrigationLine; action: CheckAction; forRun: boolean; onClose: () => void }) {
  const toast = useToast();
  const checkThenRun = useCheckThenRun();
  // A line with its check recorded asks for none again; only Run… opens the sheet on one (to enable it).
  const asks = !line.check;
  const [checked, setChecked] = useState(false);
  const [note, setNote] = useState("");
  const [manual, setManual] = useState(MANUAL_DEFAULT);
  const [reason, setReason] = useState("");
  const words = WORDS[action];
  const write = useProgramWrite("API-IRR-08", {
    onSaved: () => {
      toast.show({ message: `Requested: ${lineDisplayName(line)} ${words.done}${asks ? ", check recorded" : ""}` });
      if (forRun) checkThenRun(line.line_id); else onClose();
    },
  });
  const save = () => void write.send({ line_id: line.line_id }, {
    ...(action === "enable" ? { enabled: true } : { schedule_member: true }),
    ...(asks ? { checked: true } : {}),
    ...(asks && note.trim() ? { check_note: note.trim() } : {}),
    manual,
    ...(reason.trim() ? { reason: reason.trim() } : {}),
  });
  const boxError = errorFor(write.error, "checked");
  return <EditDrawer open onClose={onClose} title={`${words.title} ${lineDisplayName(line)}`} onSave={save} saving={write.sending} saveLabel={words.save} saveDisabled={asks && !checked}
    status={<>
      {forRun && <p className="state-note" data-run-then="">{RUN_THEN_TEXT}</p>}
      {asks && <p className="state-note">Asked once for this line: your name and the time are recorded with it.</p>}
    </>} error={formError(write.error, ["checked", "check_note", "reason"])}>
    {asks && <>
      <label className="check field-check">
        <input type="checkbox" name="checked" checked={checked} onChange={(event) => setChecked(event.target.checked)} />
        <span>{checkedLabel(line.line_id)}</span>
      </label>
      {boxError && <FieldError id={`${line.line_id}-checked-error`}>{boxError}</FieldError>}
      <Reason label="Note (optional)" value={note} onChange={setNote} error={errorFor(write.error, "check_note")} />
    </>}
    <ManualToggle checked={manual} onChange={setManual} />
    <Reason value={reason} onChange={setReason} error={errorFor(write.error, "reason")} />
  </EditDrawer>;
}

/**
 * The check sheet (`?check=<line_id>`, the one edit drawer): "Checked: <the line's check>", Note, Manual (ticked) and
 * Reason. Its one action stays disabled until Checked is ticked, then sends `{enabled: true, checked: true, check_note,
 * manual, reason}`, or `{schedule_member: true, ...}` for a mister's Schedule; the API takes who and when from the
 * session. A viewer, or a link to a line with nothing to ask, gets no sheet.
 */
export function CheckSheet({ lines }: { lines: readonly IrrigationLine[] }) {
  const operator = useRole() === "operator";
  const { value, wanted, close } = useWaterDrawer("check");
  // Fog too (#1343 follow-up): enabling it before its first run-once asks its check like a wet line.
  const line = boardLines(lines).find((item) => item.line_id === value);
  const action = operator && line ? checkAction(line, wanted) : null;
  if (!line || !action) return null;
  return <CheckForm key={`${line.line_id}-${action}`} line={line} action={action} forRun={wanted === "run"} onClose={close} />;
}
