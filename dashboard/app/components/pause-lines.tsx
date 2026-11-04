// SPDX-License-Identifier: MIT
import { useState } from "react";
import { localWallClock, wallClockInstant } from "../lib/format";
import type { IrrigationLine } from "../lib/operator-api";
import { useRole } from "../lib/session";
import { localDay, pauseUntilWords, pausedNow, useWaterDrawer, wetLines } from "../lib/water";
import { errorFor, formError, useProgramWrite } from "../lib/writes";
import { Button } from "./button";
import { EditDrawer, FieldError, FieldHint, Reason, Select, describedBy, useFieldIds } from "./forms";
import { useClockValue, useServerClockSkew } from "./freshness";
import { useToast } from "./toast";

/** What a pause does and does not stop, in words (irrigation §4.2). */
export const PAUSE_TEXT = "A pause holds scheduled and run-now water. VPD misting continues: turn VPD control off, or disable the line, to stop it.";
/** `line_id` for every line. */
const ALL = "all";
const HOUR_MS = 3_600_000;

/** The Until the drawer starts with: the next whole hour at least 30 minutes ahead (10:42 reads 12:00). */
export const defaultPauseUntil = (now: number): number => Math.ceil((now + HOUR_MS / 2) / HOUR_MS) * HOUR_MS;
/** Now on the server's clock as `paused_until`, which resumes a line. */
const resumeBody = (skewMs: number) => ({ paused_until: new Date(Date.now() - skewMs).toISOString() });

function PauseForm({ lines, target, onClose }: { lines: readonly IrrigationLine[]; target: string; onClose: () => void }) {
  const toast = useToast();
  const skew = useServerClockSkew();
  const today = useClockValue(localDay);
  const ids = useFieldIds();
  const choices = [ALL, ...wetLines(lines).map((line) => line.line_id)];
  const [chosen, setChosen] = useState(target);
  const [until, setUntil] = useState(() => localWallClock(defaultPauseUntil(Date.now() - skew)));
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState<string | null>(null);
  const write = useProgramWrite("API-IRR-08");
  const save = async () => {
    const at = wallClockInstant(until);
    // A time already past would resume the line, not pause it.
    const problem = at === null ? "Enter a date and time" : Date.parse(at) <= Date.now() - skew ? "Pick a time after now" : null;
    setTyped(problem);
    if (at === null || problem) return;
    const outcome = await write.send({ line_id: chosen }, { paused_until: at, ...(reason.trim() ? { reason: reason.trim() } : {}) });
    if (!outcome.ok) return;
    toast.show({ message: `Requested: ${chosen === ALL ? "all lines" : chosen} paused until ${pauseUntilWords(at, today)}` });
    onClose();
  };
  const untilError = typed ?? errorFor(write.error, "paused_until");
  const label = (id: string) => {
    const line = lines.find((item) => item.line_id === id);
    return id === ALL ? "All lines" : line ? `${id} ${line.name}` : id;
  };
  return <EditDrawer open onClose={onClose} title="Pause lines" status={<p className="state-note">{PAUSE_TEXT}</p>} onSave={() => void save()} saving={write.sending} saveLabel="Pause"
    error={formError(write.error, ["paused_until", "reason"])}>
    <Select label="Line" value={choices.indexOf(chosen)} options={choices.map((id, index) => ({ value: index, label: label(id) }))} onChange={(index) => setChosen(choices[index])} />
    <div className={untilError ? "field field-invalid" : "field"}>
      <label htmlFor={ids.id}>Until</label>
      <input id={ids.id} type="datetime-local" step={60} value={until} aria-invalid={untilError ? true : undefined}
        aria-describedby={describedBy(ids.hintId, untilError && ids.errorId)} onChange={(event) => setUntil(event.target.value)} />
      <FieldHint id={ids.hintId}>America/Denver</FieldHint>
      {untilError && <FieldError id={ids.errorId}>{untilError}</FieldError>}
    </div>
    <Reason value={reason} onChange={setReason} error={errorFor(write.error, "reason")} />
  </EditDrawer>;
}

/**
 * "Pause lines…" and, while any line is paused, "Resume all", for operators: the actions of Water → Today's page header
 * (redesign §5.2). Pause lines… opens the drawer (`?pause=all`); Resume all sends `{paused_until: <now>}` to `all`.
 * Neither carries `manual`. Viewers get neither; until API-IRR-01 answers there is no line to pause.
 */
export function PauseActions({ lines }: { lines: readonly IrrigationLine[] | undefined }) {
  const operator = useRole() === "operator";
  const { open } = useWaterDrawer("pause");
  const resume = useProgramWrite("API-IRR-08");
  const skew = useServerClockSkew();
  // Any line, L03 and fog included: they have no row or Resume of their own, and `all` is what pauses and clears them.
  const anyPaused = useClockValue((now) => (lines ?? []).some((line) => pausedNow(line, now)));
  if (!operator) return null;
  return <>
    {anyPaused && <Button write busy={resume.sending} onClick={() => void resume.send({ line_id: ALL }, resumeBody(skew))}>Resume all</Button>}
    <Button write disabled={!lines} aria-haspopup="dialog" onClick={() => open(ALL)}>Pause lines…</Button>
    {resume.error && <p className="field-error" role="alert">{resume.error.message}</p>}
  </>;
}

/**
 * The pause drawer (`?pause=<line_id|all>`, the one edit drawer), on all three Water views, so a line's "Pause until…"
 * and a link open it wherever they are: a Line, one or "All lines", Until (a local date and time, which must be ahead)
 * and Reason, sending `PUT /v1/irrigation/lines/{line_id}` or `/lines/all` with `{paused_until, reason}`. Viewers get none.
 */
export function PauseDrawer({ lines }: { lines: readonly IrrigationLine[] | undefined }) {
  const operator = useRole() === "operator";
  const { value, close } = useWaterDrawer("pause");
  if (!operator || !lines) return null;
  const target = value === ALL || wetLines(lines).some((line) => line.line_id === value) ? value : null;
  return target ? <PauseForm key={target} lines={lines} target={target} onClose={close} /> : null;
}

/** "Resume" on a paused line's card, for operators: `{paused_until: <now>}`, with no `manual`. Nothing while it is not paused. */
export function ResumeLine({ line }: { line: Pick<IrrigationLine, "line_id" | "paused_until"> }) {
  const write = useProgramWrite("API-IRR-08");
  const skew = useServerClockSkew();
  const paused = useClockValue((now) => pausedNow(line, now));
  if (!paused) return null;
  return <>
    <Button write busy={write.sending} onClick={() => void write.send({ line_id: line.line_id }, resumeBody(skew))}>Resume <span className="screen-reader">{line.line_id}</span></Button>
    {write.error && <span className="field-error" role="alert">{write.error.message}</span>}
  </>;
}

/** "Pause <line> until after this run" in a block's popover (redesign §6.2), for operators; `until` comes from `pauseAfterRun`. */
export function PauseAfterRun({ lineId, until }: { lineId: string; until: string }) {
  const write = useProgramWrite("API-IRR-08");
  return <>
    <Button write busy={write.sending} onClick={() => void write.send({ line_id: lineId }, { paused_until: until })}>Pause {lineId} until after this run</Button>
    {write.error && <span className="field-error" role="alert">{write.error.message}</span>}
  </>;
}
