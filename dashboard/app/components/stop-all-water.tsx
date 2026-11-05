// SPDX-License-Identifier: MIT
import { useStop } from "../lib/writes";
import { Button } from "./button";
import { cls } from "./common";

/** A Stop the caller already holds: Now's route keeps `useStop` so its reads speed up while the Stop confirms. */
export type StopAllControl = { stop: () => void; sending: boolean };

/**
 * Stop all water (redesign §2.5), the one component every place that offers it uses: API-IRR-07 `{}` through
 * `useStop`, operators only, one tap, no dialog, one Idempotency-Key per tap, "Sending…" until the 202 and then the
 * Stop result toast. Filled red, and never disabled by the writes flag. It lives in exactly three places: Now's quick
 * actions, the Water header (all three tabs) and a fence-breach alert. `compact` reads "■ Stop all" at 860 px and
 * below, where it shares Water's sticky tab row; `wide` is full width on phones. The ■ is drawn (`.glyph-stop`): the
 * self-hosted IBM Plex has no geometric shapes, so a character would fall back to another font.
 */
export function StopAllWater({ compact = false, wide = false, control }: { compact?: boolean; wide?: boolean; control?: StopAllControl }) {
  const own = useStop();
  const sending = control ? control.sending : own.sending;
  const stop = control ? control.stop : () => { void own.stop(); };
  return <Button variant="stop" write wide={wide} busy={sending} className={cls("stop-all-water", compact && "stop-all-water-compact")} onClick={stop}>
    <span className="glyph glyph-stop" aria-hidden="true" />Stop all <span className="stop-all-water-word">water</span>
  </Button>;
}
