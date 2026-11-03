// SPDX-License-Identifier: MIT
import { useState } from "react";
import type { IrrigationLine } from "../lib/operator-api";
import { useRole } from "../lib/session";
import { outOfRange, parseWhole } from "../lib/setting-values";
import { headroomWords, useWaterDrawer, wetLines, zoneWord } from "../lib/water";
import { MANUAL_DEFAULT, errorFor, formError, useProgramWrite, type FieldUnits } from "../lib/writes";
import { Button } from "./button";
import { EditDrawer, ManualToggle, NumberField, Reason } from "./forms";
import { useToast } from "./toast";

/** `daily_cap_min` and `vpd_reserve_min` as the contract bounds them (`LineUpdate`); a reserve above the cap is the API's refusal. */
const MINUTES = { min: 0, max: 480, unit: "min", step: 1 } as const;
const WHOLE_MINUTES = "Enter whole minutes";
/** Both fields are in minutes, so a 422's "Must be 0–130 min" reads with its unit. */
const unitsOf = (): FieldUnits => ({ unit: MINUTES.unit });

function OptionsForm({ line, onClose }: { line: IrrigationLine; onClose: () => void }) {
  const toast = useToast();
  const mister = line.kind === "mister";
  const [cap, setCap] = useState(String(line.daily_cap_min));
  const [reserve, setReserve] = useState(String(line.vpd_reserve_min ?? 0));
  const [manual, setManual] = useState(MANUAL_DEFAULT);
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState<{ cap: string | null; reserve: string | null }>({ cap: null, reserve: null });
  const write = useProgramWrite("API-IRR-08", { unitsOf });
  const capValue = parseWhole(cap);
  const reserveValue = parseWhole(reserve);
  const save = async () => {
    const capError = capValue === null ? WHOLE_MINUTES : outOfRange(capValue, MINUTES);
    const reserveError = !mister ? null : reserveValue === null ? WHOLE_MINUTES : outOfRange(reserveValue, MINUTES);
    setTyped({ cap: capError, reserve: reserveError });
    if (capError || reserveError || capValue === null) return;
    const outcome = await write.send({ line_id: line.line_id }, {
      daily_cap_min: capValue,
      ...(mister && reserveValue !== null ? { vpd_reserve_min: reserveValue } : {}),
      manual,
      ...(reason.trim() ? { reason: reason.trim() } : {}),
    });
    if (!outcome.ok) return;
    toast.show({ message: `Requested: ${line.line_id} daily cap ${capValue > 0 ? `${capValue} min` : "none"}${mister ? `, ${reserveValue} min kept for VPD` : ""}` });
    onClose();
  };
  const aboveCap = capValue !== null && reserveValue !== null && reserveValue > capValue;
  return <EditDrawer open onClose={onClose} title="Options" eyebrow={`${line.line_id} · ${zoneWord(line.zone)}`} heading={line.name} onSave={() => void save()} saving={write.sending}
    error={formError(write.error, ["daily_cap_min", "vpd_reserve_min", "reason"])}>
    <NumberField label="Daily cap" value={cap} onChange={setCap} {...MINUTES} error={typed.cap ?? errorFor(write.error, "daily_cap_min")}
      hint={<>0 means none · <span data-headroom>{headroomWords(line.today?.minutes, capValue)}</span></>} />
    {mister && <NumberField label="VPD reserve" value={reserve} onChange={setReserve} {...MINUTES} max={capValue !== null && capValue >= 0 && capValue <= MINUTES.max ? capValue : MINUTES.max}
      error={typed.reserve ?? errorFor(write.error, "vpd_reserve_min")}
      hint={aboveCap ? <span className="field-flag">Above the {capValue} min cap: this will be refused</span> : "Kept for VPD: schedules stop this many minutes short of the cap"} />}
    <ManualToggle checked={manual} onChange={setManual} />
    <Reason value={reason} onChange={setReason} error={errorFor(write.error, "reason")} />
  </EditDrawer>;
}

/** "Options" on a line card, for operators: opens `?options=<line_id>`. */
export function OptionsButton({ line }: { line: Pick<IrrigationLine, "line_id"> }) {
  const { open } = useWaterDrawer("options");
  return <Button write aria-haspopup="dialog" onClick={() => open(line.line_id)}>Options <span className="screen-reader">of {line.line_id}</span></Button>;
}

/**
 * Line options (`?options=<line_id>`, the one edit drawer): the daily cap 0–480 with today's minutes and headroom beside
 * it; the VPD reserve on L04, L07 and L09, whose hint flags one above the cap, which the API refuses; Manual (ticked)
 * and Reason. Save sends `{daily_cap_min, manual, reason}` and the reserve on those three; a 422 shows beside its field.
 * The site caps are not here: the limits strip's SAF-4 links to their Settings drawer. A viewer gets no drawer.
 */
export function LineOptions({ lines }: { lines: readonly IrrigationLine[] }) {
  const operator = useRole() === "operator";
  const { value, close } = useWaterDrawer("options");
  const line = wetLines(lines).find((item) => item.line_id === value);
  if (!operator || !line) return null;
  return <OptionsForm key={line.line_id} line={line} onClose={close} />;
}
