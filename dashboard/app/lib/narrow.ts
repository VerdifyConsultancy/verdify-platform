// SPDX-License-Identifier: MIT
import { useSyncExternalStore } from "react";

/** The phone breakpoint the stylesheet and the shell use (screens §1.1): 860 px and below. */
export const PHONE_QUERY = "(max-width: 860px)";

const matches = () => typeof globalThis.matchMedia === "function" && globalThis.matchMedia(PHONE_QUERY).matches;
function subscribe(change: () => void): () => void {
  if (typeof globalThis.matchMedia !== "function") return () => {};
  const query = globalThis.matchMedia(PHONE_QUERY);
  query.addEventListener("change", change);
  return () => query.removeEventListener("change", change);
}

/**
 * True at phone width, following the viewport, for layouts CSS alone cannot give (a different order, a fold). The
 * server and the hydrating render read the wide value, so the hydrated markup matches; React then re-renders with the
 * phone value. Chart code has its own (`useNarrow` in charts/uplot-chart.tsx), which only charting routes load.
 */
export function usePhone(): boolean {
  return useSyncExternalStore(subscribe, matches, () => false);
}
