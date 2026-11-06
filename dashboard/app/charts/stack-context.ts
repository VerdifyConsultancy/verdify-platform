// SPDX-License-Identifier: MIT
import { createContext } from "react";

/**
 * True for a chart drawn inside a ChartStack (redesign §7.1): the lanes then take the panels' drawing area and draw no
 * time axis of their own, so the stack reads as one instrument with one axis. Its own module, so a page that draws lanes
 * alone (Equipment) loads no stack code.
 */
export const InStack = createContext(false);

/** A lane span an event touched (#1355): the rows holding `equipment` outline [from, to]. */
export type LitLane = { readonly equipment: string; readonly from: number; readonly to: number };
/**
 * The link between a stack's event rail and its lanes (#1355): the spans the hovered mark touched, which the lanes
 * outline, and the lane span under the pointer, which lights the marks that touched it. Null outside a stack.
 */
export type StackLinkValue = {
  readonly lit: readonly LitLane[];
  /** The row's equipment keys and the instant under the pointer; null when the pointer leaves the lanes. */
  readonly onLane: (equipment: readonly string[] | null, at: number | null) => void;
};
export const StackLink = createContext<StackLinkValue | null>(null);
