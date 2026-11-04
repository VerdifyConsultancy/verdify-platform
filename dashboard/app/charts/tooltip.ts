// SPDX-License-Identifier: MIT
import type uPlot from "uplot";

/**
 * One tooltip per chart, inside `plot.over` (design system §5.3), shown only by the chart under the pointer when charts
 * share a sync key. Its lines are set with `textContent` only, and it moves
 * with `style.transform` (CSSOM), which the `style-src 'self'` CSP allows; no markup carries a style attribute. The data
 * table is the keyboard and screen-reader equivalent, so the tooltip is hidden from assistive technology.
 */
export type TooltipLines = (plot: uPlot) => readonly string[] | null;

/**
 * The chart each sync key's pointer is over. Charts on one key share a cursor, and only the one under the pointer shows
 * its tooltip (redesign §7.1: one tooltip per synced group, never a stack of boxes). A cursor set with no pointer on any
 * chart of the key (a programmatic `setCursor`) shows its tooltip as before.
 */
const pointerOn = new Map<string, uPlot>();
/** True while the pointer is over another chart on `syncKey`. */
export function pointerElsewhere(plot: uPlot, syncKey: string | undefined): boolean {
  const owner = syncKey ? pointerOn.get(syncKey) : undefined;
  return owner !== undefined && owner !== plot;
}

export function tooltipPlugin(lines: TooltipLines, syncKey?: string): uPlot.Plugin {
  let box: HTMLDivElement | null = null;
  let release = () => {};
  return {
    hooks: {
      ready: (plot: uPlot) => {
        box = document.createElement("div");
        box.className = "chart-tooltip";
        box.hidden = true;
        box.setAttribute("aria-hidden", "true");
        plot.over.appendChild(box);
        if (!syncKey) return;
        const enter = () => { pointerOn.set(syncKey, plot); };
        const leave = () => { if (pointerOn.get(syncKey) === plot) pointerOn.delete(syncKey); };
        plot.over.addEventListener("mouseenter", enter);
        plot.over.addEventListener("mouseleave", leave);
        release = () => { leave(); plot.over.removeEventListener("mouseenter", enter); plot.over.removeEventListener("mouseleave", leave); };
      },
      setCursor: (plot: uPlot) => {
        if (!box) return;
        if (pointerElsewhere(plot, syncKey)) { box.hidden = true; return; }
        const text = lines(plot);
        if (!text || !text.length) { box.hidden = true; return; }
        box.replaceChildren(...text.map((line) => {
          const row = document.createElement("div");
          row.textContent = line;
          return row;
        }));
        box.hidden = false;
        place(plot, box);
      },
      destroy: () => { release(); box?.remove(); box = null; },
    },
  };
}

/** Beside the cursor, flipped to its left near the right edge, kept inside the plot. */
function place(plot: uPlot, box: HTMLDivElement) {
  const left = plot.cursor.left ?? 0, top = plot.cursor.top ?? 0;
  const width = box.offsetWidth, height = box.offsetHeight;
  const room = plot.over.clientWidth, depth = plot.over.clientHeight;
  let x = left + 12;
  if (x + width > room) x = Math.max(0, left - width - 12);
  const y = Math.min(Math.max(0, top - height / 2), Math.max(0, depth - height));
  box.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
}

/** The cursor's x value, or null while it is off the plot. */
export function cursorX(plot: uPlot): number | null {
  const left = plot.cursor.left;
  if (left === undefined || left === null || left < 0) return null;
  return plot.posToVal(left, "x");
}
