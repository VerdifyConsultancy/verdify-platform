// SPDX-License-Identifier: MIT
import { useSyncExternalStore } from "react";

/**
 * The three breakpoints of redesign §3.2, each with one job, declared once here and mirrored by the stylesheets'
 * media queries (`@media(max-width:600px)`, `(max-width:860px)` and `(min-width:1200px)`):
 * - `PHONE_PX`: card grids go from one column to two above it;
 * - `NARROW_PX`: the navigation (bottom bar or top nav), the chart axes and tables-to-rows switch at it (screens §1.1);
 * - `WIDE_PX`: the main and aside split from it.
 * Weather's 520/700/900 rules stay in weather's sections; new code does not use them.
 */
export const PHONE_PX = 600;
export const NARROW_PX = 860;
export const WIDE_PX = 1200;

/** 860 px and below: the phone bar, the phone chart axes and stacked table rows. */
export const NARROW_QUERY = `(max-width: ${NARROW_PX}px)`;
/** The same query under the name the screens use: a phone is 860 px and below (screens §1.1). */
export const PHONE_QUERY = NARROW_QUERY;

/** True at 860 px and below, now; false where there is no window (SSR, tests without a stub). */
export function narrowViewport(): boolean {
  return typeof globalThis.matchMedia === "function" && globalThis.matchMedia(NARROW_QUERY).matches;
}
function subscribe(change: () => void): () => void {
  if (typeof globalThis.matchMedia !== "function") return () => {};
  const query = globalThis.matchMedia(NARROW_QUERY);
  query.addEventListener("change", change);
  return () => query.removeEventListener("change", change);
}

/**
 * True at phone width (860 px and below), following the viewport, for behaviour CSS alone cannot give (a fold that
 * starts closed, a chart's tick spacing). Layout stays CSS: where phone and desktop differ in structure, both render and
 * the stylesheet shows one (redesign §9.6). The server and the hydrating render read the wide value, so the hydrated
 * markup matches; React then re-renders with the phone value. Charts read the same hook as `useNarrow`.
 */
export function usePhone(): boolean {
  return useSyncExternalStore(subscribe, narrowViewport, () => false);
}
