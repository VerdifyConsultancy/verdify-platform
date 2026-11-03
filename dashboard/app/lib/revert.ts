// SPDX-License-Identifier: MIT
/**
 * Revert on a setting's drawer (US-SET-06, screens SCR-05): what one tap goes back to, worked out from API-12. Pure: the
 * write itself is the ordinary API-10 or API-11 through `useSettingWrite`, so registry bounds, snapping, the
 * `crop_band_anchors` update and attribution all stay on the server, and there is no revert endpoint.
 *
 * The target is the desired row just before the newest one. `desired[]` is oldest first and starts with the row in force
 * at `from`, so a window that holds only the newest row may just be too short: read 90 days back once
 * (`needsLookback`), and with still no earlier row Revert releases to auto. Iris's plan values are not desired rows, so
 * a setting with no desired row has nothing to revert (they are overridden with Save).
 *
 * One exception, for a crop-band anchor or width (`band`): API-11 leaves `crop_band_anchors` as the last write set it,
 * so a release would not bring the previous band back. When the row before the newest is a release or missing, the
 * target there is the value the device reported just before the newest row, written through API-10. A device that
 * already held the newest row's value means that write changed only Manual, so a release is still the way back.
 */
import { shownActor, type SignedIn } from "./activity";
import { monthDayClock } from "./format";
import type { DesiredSetting, SettingHistory } from "./operator-api";
import { KEPT_DECIMALS, keptValueText, type SettingShape } from "./setting-values";

export const REVERT_TO_AUTO = "Revert to auto";
/** Who a target taken from the readback is named after: nobody wrote it through the dashboard. */
export const DEVICE_WORD = "device";

/** What Revert sends: API-10 `{value, manual, reason}`, or API-11 `?reason=`; `text` is the button, `reason` its prefill. */
export type RevertTarget =
  | { readonly kind: "value"; readonly value: number; readonly text: string; readonly reason: string }
  | { readonly kind: "release"; readonly text: string; readonly reason: string };

/** The window holds only the newest row: an earlier one may lie before it. */
export const needsLookback = (desired: readonly DesiredSetting[]): boolean => desired.length === 1;

/** The stored reason reads with plain spaces, though the button keeps its no-break space before the unit. */
const plain = (text: string): string => text.replaceAll(" ", " ");
const stamp = (who: string, at: string): string => `(${who}, ${monthDayClock(at)})`;

function toValue(value: number, shape: SettingShape, who: string, at: string): RevertTarget {
  const text = `Revert to ${keptValueText(value, shape)} ${stamp(who, at)}`;
  return { kind: "value", value, text, reason: plain(text) };
}
function toAuto(row: DesiredSetting | null, me: SignedIn): RevertTarget {
  return { kind: "release", text: REVERT_TO_AUTO, reason: row ? `${REVERT_TO_AUTO} ${stamp(shownActor(row.actor, me).name, row.created_at)}` : REVERT_TO_AUTO };
}

/** A reading as a value to write: the binary32 noise off (1.100000023841858 is 1.1), as the drawer's field reads it. */
const kept = (value: number): number => Number(value.toFixed(KEPT_DECIMALS));

/** The newest readback with a value from before `at`, or null. */
function readingBefore(readback: SettingHistory["readback"], at: string): { at: string; value: number } | null {
  const limit = Date.parse(at);
  for (let index = readback.length - 1; index >= 0; index--) {
    const { at: seen, value } = readback[index];
    if (value !== null && Date.parse(seen) < limit) return { at: seen, value };
  }
  return null;
}

/**
 * What Revert does for this history, or null when it has nothing to go back to: no desired row at all (the button is
 * hidden), or a release with no value before it, where the setting is already on auto.
 */
export function revertTarget(history: Pick<SettingHistory, "desired" | "readback">, shape: SettingShape, me: SignedIn, { band = false }: { band?: boolean } = {}): RevertTarget | null {
  const rows = history.desired;
  const newest = rows[rows.length - 1];
  if (!newest) return null;
  const previous = rows.length > 1 ? rows[rows.length - 2] : null;
  if (previous && previous.value !== null) return toValue(previous.value, shape, shownActor(previous.actor, me).name, previous.created_at);
  if (newest.value === null) return null;
  const before = band ? readingBefore(history.readback, newest.created_at) : null;
  return before && kept(before.value) !== kept(newest.value) ? toValue(kept(before.value), shape, DEVICE_WORD, before.at) : toAuto(previous, me);
}
