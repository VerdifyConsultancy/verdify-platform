// SPDX-License-Identifier: MIT
import { lineDisplayName } from "../lib/lines";
import type { IrrigationLine } from "../lib/operator-api";
import { OperatorOnly } from "../lib/session";
import { useWaterDrawer } from "../lib/water";
import { useProgramWrite } from "../lib/writes";
import { FieldError, FieldHint, Switch, describedBy, useFieldIds } from "./forms";

/** What disabling does, in words beside the switch (irrigation §4.2). */
export const DISABLE_HINT = "Off stops all water on this line, VPD misting included";

/**
 * A line's enable switch (cards L01, L02 and L04–L10), for operators. Switching it off, or on once the line has its
 * check, sends `{enabled, manual: true}` at once (an inline switch saves manual on), and the card then reads REQUESTED.
 * Switching on a line with no check sends nothing: it opens the check sheet (`?check=<line_id>`), which asks once.
 */
export function LineEnable({ line }: { line: IrrigationLine }) {
  const write = useProgramWrite("API-IRR-08");
  const { open: openCheck } = useWaterDrawer("check");
  const ids = useFieldIds();
  const change = (on: boolean) => {
    if (on && !line.check) openCheck(line.line_id);
    else void write.send({ line_id: line.line_id }, { enabled: on, manual: true });
  };
  return <OperatorOnly>
    <div className="line-enable">
      <Switch write label={<>Enabled <span className="screen-reader">{lineDisplayName(line)}</span></>} checked={line.enabled} busy={write.sending} onChange={change}
        describedBy={describedBy(ids.hintId, write.error && ids.errorId)} />
      <FieldHint id={ids.hintId}>{DISABLE_HINT}</FieldHint>
      {write.error && <FieldError id={ids.errorId}>{write.error.message}</FieldError>}
    </div>
  </OperatorOnly>;
}
