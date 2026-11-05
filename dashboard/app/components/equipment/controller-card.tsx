// SPDX-License-Identifier: MIT
import { Fragment, type ReactNode } from "react";
import { formatValue } from "../../lib/format";
import type { Now } from "../../lib/operator-api";
import { useApiDown } from "../../lib/session";
import { useShellNow, useShellStatus } from "../../lib/shell";
import { cls } from "../common";
import { DataList } from "../data/data-list";
import { finite } from "../data/viz";
import { AgeLabel } from "../freshness";
import { Panel, type PanelState } from "../panel";

/** A version that may wrap only after its dots ("2026.10.30.<wbr>1412.<wbr>3240714"), never mid-number. */
function dotted(version: string): ReactNode {
  const parts = version.split(".");
  return parts.map((part, index) => <Fragment key={index}>{part}{index < parts.length - 1 && <>.<wbr /></>}</Fragment>);
}

/**
 * The controller (SCR-07, redesign §5.12): API-02 `controller`, served since R1 and shown here for the first time. Its
 * firmware, uptime and last reset reason, free heap against the 32 kB floor the firmware restarts under (#429: a heap
 * starved under it ends in a watchdog restart), the largest free block and the Wi-Fi signal, all from one diagnostics
 * row, so the card states that row's age once. While diagnostics are stale or the controller is not reporting the values
 * are muted with "Last data … ago"; while the API is down nothing in it is coloured.
 */
export const HEAP_FLOOR_KB = 32;
const NBSP = "\u00a0";
type Controller = NonNullable<Now["controller"]>;

/** "3 d 3 h", "5 h 12 min", "12 min", "40 s": how long the controller had been up when it reported. */
export function uptimeWords(seconds: number | null | undefined): string {
  if (!finite(seconds) || seconds < 0) return "—";
  const days = Math.floor(seconds / 86_400), hours = Math.floor((seconds % 86_400) / 3600), minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}${NBSP}d${hours ? `${NBSP}${hours}${NBSP}h` : ""}`;
  if (hours > 0) return `${hours}${NBSP}h${minutes ? `${NBSP}${minutes}${NBSP}min` : ""}`;
  return minutes > 0 ? `${minutes}${NBSP}min` : `${Math.floor(seconds)}${NBSP}s`;
}

/** The free heap against the floor in words: "6 kB above the 32 kB floor", or "4 kB below the 32 kB floor". */
export function heapWords(free: number | null | undefined): { readonly text: string; readonly below: boolean } | null {
  if (!finite(free)) return null;
  const gap = Math.abs(free - HEAP_FLOOR_KB);
  const floor = formatValue(HEAP_FLOOR_KB, "kB");
  if (free < HEAP_FLOOR_KB) return { text: `${formatValue(gap, "kB")} below the ${floor} floor`, below: true };
  return { text: gap < 0.5 ? `at the ${floor} floor` : `${formatValue(gap, "kB")} above the ${floor} floor`, below: false };
}

/**
 * The heap meter: a 6 px `--field` track to twice the floor (or past the value), the free heap filled in `--muted`, in
 * `--fault` below the floor, and the floor itself as a 2 px ink tick. It sits beside the numbers in words, so it is
 * `aria-hidden`.
 */
export function HeapBar({ free, stale = false }: { free: number | null | undefined; stale?: boolean }) {
  const apiDown = useApiDown();
  const scale = Math.max(HEAP_FLOOR_KB * 2, finite(free) ? free * 1.15 : 0);
  const at = (value: number) => `${Math.round(Math.min(100, Math.max(0, (value / scale) * 100)) * 100) / 100}%`;
  const below = finite(free) && free < HEAP_FLOOR_KB;
  return <svg className={cls("ctl-heap", below && !apiDown && "is-below", stale && "is-stale", apiDown && "is-neutral")} width="100%" height="10" aria-hidden="true" focusable="false">
    <rect className="ctl-heap-track" x="0" y="2" width="100%" height="6" rx="3" />
    {finite(free) && free > 0 && <rect className="ctl-heap-fill" x="0" y="2" width={at(free)} height="6" rx="3" />}
    <line className="ctl-heap-floor" x1={at(HEAP_FLOOR_KB)} x2={at(HEAP_FLOOR_KB)} y1="0" y2="10" />
  </svg>;
}

/** The controller's vitals as the card and the status line read them; stale while its diagnostics row is old. */
export function useController(): { readonly controller: Controller | null; readonly stale: boolean; readonly state: PanelState; readonly observedAt: string | null } {
  const shell = useShellNow();
  const status = useShellStatus();
  const now = shell.data;
  const controller = now?.controller ?? null;
  const observedAt = controller?.observed_at ?? now?.freshness.diagnostics.observed_at ?? null;
  const stale = Boolean(now) && (now!.freshness.diagnostics.stale || status.controllerReporting === false || status.apiDown);
  const state: PanelState = !now ? (shell.error ? "error" : "loading") : stale ? "stale" : "ready";
  return { controller, stale, state, observedAt };
}

/**
 * The Equipment status line (redesign §5.12): "Controller up 3 d 3 h · firmware … · Wi-Fi −61 dBm · Updated 45 s ago";
 * on a phone (`short`) "Up 3 d 3 h · −61 dBm · Updated 45 s ago".
 */
export function ControllerStatus({ short = false }: { short?: boolean } = {}): ReactNode {
  const { controller, stale, state, observedAt } = useController();
  if (state === "loading" || state === "error") return null;
  if (!controller) return short ? "No vitals reported" : "The controller has not reported its vitals";
  if (stale) return <>Controller · <AgeLabel observedAt={observedAt} stale /></>;
  if (short) return <>Up {uptimeWords(controller.uptime_s)} · {formatValue(controller.rssi_dbm, "dBm")} · <AgeLabel observedAt={observedAt} stale={false} /></>;
  return <>
    Controller up {uptimeWords(controller.uptime_s)} · firmware <span className="mono">{controller.firmware_version ?? "—"}</span> · Wi-Fi{" "}
    {formatValue(controller.rssi_dbm, "dBm")} · <AgeLabel observedAt={observedAt} stale={false} />
  </>;
}

export function ControllerCard() {
  const { controller, stale, state, observedAt } = useController();
  const heap = heapWords(controller?.heap_free_kb);
  const below = heap?.below === true && !stale;
  return <Panel title="Controller" thing="the controller" state={state} reserve="md" observedAt={observedAt} tone={below ? "fault" : undefined}>
    {controller
      ? <>
        <div className="ctl-heap-row">
          <p className="ctl-heap-words"><span className="eyebrow">Free heap</span>
            <span className="ctl-heap-value">{formatValue(controller.heap_free_kb, "kB")}</span>{" "}
            {heap && <span className={cls("meta", below && "ctl-heap-low")}>{below && <><span className="glyph glyph-down" aria-hidden="true" /> </>}{heap.text}</span>}
          </p>
          <HeapBar free={controller.heap_free_kb} stale={stale} />
        </div>
        <DataList className="ctl-controller-facts" items={[
          { key: "firmware", term: "Firmware", value: controller.firmware_version ? <span className="mono ctl-firmware">{dotted(controller.firmware_version)}</span> : null, reason: "not reported" },
          { key: "uptime", term: "Uptime", value: finite(controller.uptime_s) ? uptimeWords(controller.uptime_s) : null, reason: "not reported" },
          { key: "reset", term: "Last reset", value: controller.reset_reason || null, reason: "not reported" },
          { key: "block", term: "Largest block", value: finite(controller.heap_largest_block_kb) ? formatValue(controller.heap_largest_block_kb, "kB") : null, reason: "not reported" },
          { key: "rssi", term: "Wi-Fi", value: finite(controller.rssi_dbm) ? formatValue(controller.rssi_dbm, "dBm") : null, reason: "not reported" },
        ]} />
      </>
      : <p className="meta">The controller has not reported its vitals yet.</p>}
  </Panel>;
}
