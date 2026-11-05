// SPDX-License-Identifier: MIT
import { createContext } from "react";

/**
 * True for a chart drawn inside a ChartStack (redesign §7.1): the lanes then take the panels' drawing area and draw no
 * time axis of their own, so the stack reads as one instrument with one axis. Its own module, so a page that draws lanes
 * alone (Equipment) loads no stack code.
 */
export const InStack = createContext(false);
