// SPDX-License-Identifier: MIT
/**
 * Run… on a line card (SCR-04, irrigation §4.7, US-WAT-12, US-WAT-23): #1125's `ConfirmDialog` on the C hook, the app's
 * one confirmation. `lines[].kind` picks the fields. A disabled line offers no Run… (the API's `line_disabled` covers a
 * race), a mister offers "Mist now…" only while `schedule_member` is on (D-IRP-11), and fog and the fertilizer master
 * have no card controls. A refusal keeps the dialog open with the code in words; nothing else is sent and nothing queues.
 *
 * Before the irrigation firmware only L01 runs, through the bridge, for the controller's own `irrig_wall_duration_min`:
 * no Duration field, the value read from API-09 when the dialog opens (without it the dialog names the setting only), and
 * a body of `{}` plus the optional reason. Other lines say when Run arrives. The engine flag is API-IRR-01's alone.
 */
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { IrrigationLine } from "../lib/operator-api";
import { fetchRead } from "../lib/queries";
import { OperatorOnly } from "../lib/session";
import { useCommand } from "../lib/writes";
import { Button } from "./button";
import { ConfirmDialog, type RunBody } from "./forms/confirm-dialog";

export const RUN_ARRIVES_TEXT = "Run arrives with the irrigation firmware";
/** The API-09 row whose device value is how long the bridge's L01 run lasts (irrigation §6.5). */
export const WALL_DURATION_PARAM = "irrig_wall_duration_min";
/** The one line the bridge runs. */
const BRIDGE_LINE = "L01";

/** What a line's card offers: Run…, Mist now…, the words that Run arrives with the firmware, or nothing. */
export type RunOffer = "run" | "mist" | "arrives" | null;
export function runOffer(line: Pick<IrrigationLine, "line_id" | "kind" | "enabled" | "schedule_member">, engine: boolean): RunOffer {
  if (line.kind === "fog" || line.kind === "master") return null;
  if (!engine && line.line_id !== BRIDGE_LINE) return "arrives";
  if (!line.enabled) return null;
  if (line.kind === "mister") return line.schedule_member === true ? "mist" : null;
  return "run";
}

/** The row's device value from one API-09 read when `open` turns true; null until it answers, or when it has none. */
function useWallDuration(open: boolean): number | null {
  const client = useQueryClient();
  const [minutes, setMinutes] = useState<number | null>(null);
  useEffect(() => {
    if (!open) return;
    let current = true;
    fetchRead(client, "API-09", {}).then(
      (settings) => { if (current) setMinutes(settings.items.find((row) => row.param === WALL_DURATION_PARAM)?.readback?.value ?? null); },
      () => { if (current) setMinutes(null); },
    );
    return () => { current = false; };
  }, [client, open]);
  return minutes;
}

function RunButton({ line, engine, label }: { line: IrrigationLine; engine: boolean; label: string }) {
  const [open, setOpen] = useState(false);
  const command = useCommand({ operation: "API-IRR-06" });
  const fixed = useWallDuration(open && !engine);
  const close = () => { setOpen(false); command.clearError(); };
  const run = async (body: RunBody) => (await command.send({ line_id: line.line_id }, body)).ok;
  // The relay edge answers from here on: RUNNING on the card, or within 30 s the line's reason or "Not started".
  const running = line.state.code === "running";
  const note = command.phase === "waiting" && !running ? "Waiting for the valve to open" : command.phase === "late" && !running ? command.message : null;
  return <>
    <Button write wide onClick={() => setOpen(true)}>{label} <span className="screen-reader">{line.line_id}</span></Button>
    {note && <span className="state-note" data-run-note={line.line_id}>{note}</span>}
    {open && <ConfirmDialog open onClose={close} line={line} fixed={engine ? null : { minutes: fixed }} onRun={run} sending={command.sending} error={command.error} />}
  </>;
}

/** The line card's Run… or Mist now…, for operators only; disabled with changes switched off (`Button write`). */
export function RunControl({ line, engine }: { line: IrrigationLine; engine: boolean }) {
  const offer = runOffer(line, engine);
  if (offer === null) return null;
  return <OperatorOnly>
    {offer === "arrives"
      ? <span className="state-note" data-arrives="run">{RUN_ARRIVES_TEXT}</span>
      : <RunButton line={line} engine={engine} label={offer === "mist" ? "Mist now…" : "Run…"} />}
  </OperatorOnly>;
}
