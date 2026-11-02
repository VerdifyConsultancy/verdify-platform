// SPDX-License-Identifier: MIT
import type uPlot from "uplot";

/**
 * One tooltip per chart, inside `plot.over` (design system §5.3). Its lines are set with `textContent` only, and it moves
 * with `style.transform` (CSSOM), which the `style-src 'self'` CSP allows; no markup carries a style attribute. The data
 * table is the keyboard and screen-reader equivalent, so the tooltip is hidden from assistive technology.
 */
export type TooltipLines = (plot: uPlot) => readonly string[] | null;

export function tooltipPlugin(lines: TooltipLines): uPlot.Plugin {
  let box: HTMLDivElement | null = null;
  return {
    hooks: {
      ready: (plot: uPlot) => {
        box = document.createElement("div");
        box.className = "chart-tooltip";
        box.hidden = true;
        box.setAttribute("aria-hidden", "true");
        plot.over.appendChild(box);
      },
      setCursor: (plot: uPlot) => {
        if (!box) return;
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
      destroy: () => { box?.remove(); box = null; },
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
