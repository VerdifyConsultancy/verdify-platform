// SPDX-License-Identifier: MIT
/**
 * Run… on a line card (SCR-04, irrigation §4.7, US-WAT-12, US-WAT-23): #1125's `ConfirmDialog` on the C hook, the app's
 * one confirmation. `lines[].kind` picks the fields. Any wet line runs once (#1343, superseding D-IRP-11): a mister offers
 * "Mist now…" whether or not Schedule is on, and a disabled line (not a reserved one) keeps its button, which opens the
 * check sheet to enable it (`?check=<line>&for=run`, asking the one-time check if none is recorded) and then this dialog.
 * Fog runs once too (#1343 follow-up): "Fog once…" for 1–30 min, which the API refuses `unavailable` until the
 * controller runs the firmware that takes it. The fertilizer master has no card controls: it opens only with a fertilized
 * run (#1336). A refusal keeps the dialog open with the code in words; nothing else is sent and nothing queues.
 *
 * Before the irrigation firmware only L01 runs, through the bridge, for the controller's own `irrig_wall_duration_min`:
 * no Duration field, the value read from API-09 when the dialog opens (without it the dialog names the setting only), and
 * a body of `{}` plus the optional reason. Other lines say when Run arrives. The engine flag is API-IRR-01's alone.
 *
 * The dialog opens from the URL (`?run=<line_id>`, as VD-69's drawers do) and it and each line's command live in the Water
 * layout (`WaterRuns`), so a failed status read, which replaces the Board with its error, leaves an open dialog and what
 * was typed in it. A card's button opens it, and its notes come back through context.
 */
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import { fetchRead } from "../lib/queries";
import { OperatorOnly, useRole } from "../lib/session";
import { boardLines, useWaterDrawer } from "../lib/water";
import { useCommand } from "../lib/writes";
import { Button } from "./button";
import { ConfirmDialog, type RunBody } from "./forms/confirm-dialog";

export const RUN_ARRIVES_TEXT = "Run arrives with the irrigation firmware";
/** The API-09 row whose device value is how long the bridge's L01 run lasts (irrigation §6.5). */
export const WALL_DURATION_PARAM = "irrig_wall_duration_min";
/** The one line the bridge runs. */
const BRIDGE_LINE = "L01";

/**
 * What a line's card offers: Run…, Mist now…, the same button on a disabled line that enables it first ("enable"), the
 * words that Run arrives with the firmware, or nothing. A reserved line has no valve wired, so it offers nothing while off.
 */
export type RunOffer = "run" | "mist" | "enable" | "arrives" | null;
export function runOffer(line: Pick<IrrigationLine, "line_id" | "kind" | "enabled" | "reserved">, engine: boolean): RunOffer {
  if (line.kind === "master") return null;
  // Fog runs once under the climate path's own rails, enabled or not; before the engine it has nothing to run.
  if (line.kind === "fog") return engine ? "run" : null;
  if (!engine && line.line_id !== BRIDGE_LINE) return "arrives";
  if (!line.enabled) return engine && !line.reserved ? "enable" : null;
  return line.kind === "mister" ? "mist" : "run";
}
/** The button's words for an offer: a mister mists, every other wet line runs. */
const runLabel = (line: Pick<IrrigationLine, "kind">): string => (line.kind === "mister" ? "Mist now…" : line.kind === "fog" ? "Fog once…" : "Run…");
/** A fog run-once's bounds and default (#1343 follow-up): 1–30 min of continuous fog. */
export const FOG_RUN_BOUNDS = { minutes: { min: 1, max: 30 } } as const;
const FOG_RUN_DEFAULTS = { minutes: 5 } as const;
/** The lines a card can run once: every wet line and fog; never L03. */
export const runLines = (lines: readonly IrrigationLine[]): IrrigationLine[] => boardLines(lines);
/** What a disabled line's button says beside it (#1343). */
export const LINE_OFF_TEXT = "This line is off: enable it to run it";

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

/** What a line's card says of its last run: the readback's note, and a refusal that answered a run the dialog was cancelled during. */
type RunNote = { readonly note: string | null; readonly refusal: string | null };
const RunNotes = createContext<Readonly<Record<string, RunNote>>>({});

/** One line's run: its command, and its dialog while `?run=` names it. A link to a line that offers no Run… opens nothing. */
function LineRun({ line, engine, publish }: { line: IrrigationLine; engine: boolean; publish: (lineId: string, note: RunNote) => void }) {
  const { value, close } = useWaterDrawer("run");
  const offer = runOffer(line, engine);
  const open = value === line.line_id && (offer === "run" || offer === "mist");
  const command = useCommand({ operation: "API-IRR-06" });
  const { clearError } = command;
  const fixed = useWallDuration(open && !engine);
  // Opening starts clean, and so does closing: only a refusal that answers after the dialog closed stays on the card.
  useEffect(() => clearError(), [open, clearError]);
  const run = async (body: RunBody) => (await command.send({ line_id: line.line_id }, body)).ok;
  // The relay edge answers from here on: RUNNING on the card, or within 30 s the line's reason or "Not started".
  const running = line.state.code === "running";
  const note = command.phase === "waiting" && !running ? "Waiting for the valve to open" : command.phase === "late" && !running ? command.message : null;
  const refusal = !open ? command.error?.message ?? null : null;
  useEffect(() => publish(line.line_id, { note, refusal }), [publish, line.line_id, note, refusal]);
  const fog = line.kind === "fog";
  return open ? <ConfirmDialog open onClose={close} line={line} fixed={engine ? null : { minutes: fixed }} onRun={run} sending={command.sending} error={command.error}
    bounds={fog ? FOG_RUN_BOUNDS : undefined} defaults={fog ? FOG_RUN_DEFAULTS : undefined} /> : null;
}

/**
 * Holds every line's run in the Water layout, around the view and for operators only, once API-IRR-01 has answered. The
 * read's data stays through a failed poll, so this does too.
 */
export function WaterRuns({ status, children }: { status: Pick<IrrigationStatus, "lines" | "engine"> | undefined; children?: ReactNode }) {
  const operator = useRole() === "operator";
  const [notes, setNotes] = useState<Readonly<Record<string, RunNote>>>({});
  const publish = useCallback((lineId: string, note: RunNote) => setNotes((current) => ({ ...current, [lineId]: note })), []);
  return <RunNotes.Provider value={notes}>
    {children}
    {operator && status && runLines(status.lines).map((line) => <LineRun key={line.line_id} line={line} engine={status.engine} publish={publish} />)}
  </RunNotes.Provider>;
}

function RunButton({ line, label, off = false }: { line: IrrigationLine; label: string; off?: boolean }) {
  const { open } = useWaterDrawer("run");
  const { open: openCheck } = useWaterDrawer("check");
  const { note, refusal } = useContext(RunNotes)[line.line_id] ?? {};
  if (off) return <>
    <Button write wide aria-haspopup="dialog" onClick={() => openCheck(line.line_id, "run")}>{label} <span className="screen-reader">{line.line_id}</span></Button>
    <span className="state-note" data-run-note={line.line_id}>{LINE_OFF_TEXT}</span>
  </>;
  return <>
    <Button write wide aria-haspopup="dialog" onClick={() => open(line.line_id)}>{label} <span className="screen-reader">{line.line_id}</span></Button>
    {note && <span className="state-note" data-run-note={line.line_id}>{note}</span>}
    {refusal && <span className="field-error" role="alert">{refusal}</span>}
  </>;
}

/**
 * The line card's Run… or Mist now…, for operators only; disabled with changes switched off (`Button write`). On a
 * disabled line the same button opens the check sheet to enable it, and the run dialog follows (#1343).
 */
export function RunControl({ line, engine }: { line: IrrigationLine; engine: boolean }) {
  const offer = runOffer(line, engine);
  if (offer === null) return null;
  return <OperatorOnly>
    {offer === "arrives"
      ? <span className="state-note" data-arrives="run">{RUN_ARRIVES_TEXT}</span>
      : <RunButton line={line} label={runLabel(line)} off={offer === "enable"} />}
  </OperatorOnly>;
}
