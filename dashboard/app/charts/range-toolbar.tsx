// SPDX-License-Identifier: MIT
import type { ReactNode } from "react";
import { Link } from "react-router";
import type { XRange } from "./types";

/**
 * RangeToolbar (redesign §7.1), which replaces RangeChips: the page's presets as nav chips, ‹ and › to step the window
 * by its own length, Zoom out (×2, centred) and Custom… (the From and To days). Every one is a link the caller builds
 * (Graphs keeps the window in its URL), so each pushes history and Back restores the previous view. Drag-to-zoom on fine
 * pointers and Reset zoom stay on the charts. The caller's own tools (Preset, Layers, + Metric, Export) follow at the end.
 * At 860 px and below (redesign §5.5) it is two rows: the presets with Custom… as a chip, then ‹ › and the tools; a
 * `wide` preset and Zoom out are left out there.
 */
export type RangePreset = {
  readonly id: string; readonly label: string; readonly href: string; readonly current: boolean;
  /** Left out at 860 px and below, so a phone's presets and Custom… fit one row (redesign §5.5: 90 d). */
  readonly wide?: boolean;
};

/** The window moved by its own length: back (-1) or forward (+1). */
export function stepWindow(window: XRange, direction: -1 | 1): XRange {
  const length = window.to - window.from;
  return { from: window.from + direction * length, to: window.to + direction * length };
}
/** The window `factor` times as long around the same centre, never past `ceiling` seconds (400 days by default). */
export function zoomOutWindow(window: XRange, factor = 2, ceiling = 400 * 86_400): XRange {
  const length = Math.min((window.to - window.from) * factor, ceiling);
  const centre = (window.from + window.to) / 2;
  return { from: Math.round(centre - length / 2), to: Math.round(centre + length / 2) };
}
/**
 * The future pad of a window that reaches now (redesign §7.1, D-UX-13): 25% of its length, at most 12 h; "Today" ends at
 * the next local midnight instead, which its caller passes as the window's end.
 */
export function futurePad(lengthSeconds: number): number {
  return Math.min(Math.max(0, lengthSeconds) * 0.25, 12 * 3600);
}

export type RangeToolbarProps = {
  presets: readonly RangePreset[];
  /** Where ‹ and › go; null where the window cannot step (the far end of what the API keeps, or into the future). */
  previous?: string | null;
  next?: string | null;
  /** The window's length in words, for the step buttons' names: "Previous 24 h", "Next 24 h". */
  span: string;
  /** Where Zoom out goes; null at the longest window. */
  zoomOut?: string | null;
  /** The Custom… form (From and To days), in a disclosure. */
  custom?: ReactNode;
  /** True while the custom form is the window in force, so its disclosure starts open. */
  customOpen?: boolean;
  /** The page's own tools after the range: Preset, Layers, + Metric, + Setting, Export. */
  children?: ReactNode;
};

function Step({ to, label, children }: { to: string | null | undefined; label: string; children: ReactNode }) {
  if (!to) return <span className="range-step" aria-disabled="true" title={`${label}: not available`}><span aria-hidden="true">{children}</span><span className="screen-reader">{label}: not available</span></span>;
  return <Link className="range-step" to={to} preventScrollReset aria-label={label}><span aria-hidden="true">{children}</span></Link>;
}

export function RangeToolbar({ presets, previous, next, span, zoomOut, custom, customOpen = false, children }: RangeToolbarProps) {
  return <div className="range-toolbar">
    <ul className="chips chips-sm range-presets" aria-label="Range">
      {presets.map((preset) => <li key={preset.id} className={preset.wide ? "range-wide-only" : undefined}>
        <Link to={preset.href} aria-current={preset.current ? "page" : undefined} preventScrollReset>{preset.label}</Link>
      </li>)}
    </ul>
    <div className="range-steps">
      <Step to={previous} label={`Previous ${span}`}>‹</Step>
      <Step to={next} label={`Next ${span}`}>›</Step>
      {zoomOut !== undefined && (zoomOut
        ? <Link className="range-zoom" to={zoomOut} preventScrollReset>Zoom out</Link>
        : <span className="range-zoom" aria-disabled="true">Zoom out</span>)}
    </div>
    {custom && <details className="range-custom" open={customOpen || undefined}><summary>Custom…</summary><div className="range-custom-body">{custom}</div></details>}
    {children && <div className="range-tools">{children}</div>}
  </div>;
}
