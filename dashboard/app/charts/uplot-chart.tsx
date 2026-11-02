// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useRef, useState } from "react";
import type uPlot from "uplot";
import uplotStyles from "uplot/dist/uPlot.min.css?url";
import { TIME_ZONE } from "../lib/format";
import chartStyles from "./charts.css?url";
import type { ChartColors } from "./draw";
import { NARROW_QUERY, narrowViewport } from "./time-axis";
import type { XRange } from "./types";

/**
 * The one uPlot wrapper (design system §5; weather `history-plot.tsx` and `linked-plots.tsx` as the base). uPlot is
 * imported inside an effect, so it is its own chunk and loads only where a chart mounts; charting route modules spread
 * `chartLinks()` into their `links`, so its stylesheet loads only there too. The wrapper owns creation, one
 * ResizeObserver calling `setSize`, `destroy()` and `disconnect()` on unmount, a height the stylesheet reserves, the
 * America/Denver `tzDate`, colours read from the design-system tokens, drag-x zoom on fine pointers only, and live data
 * that keeps the zoom.
 */
export const chartLinks = () => [{ rel: "stylesheet", href: uplotStyles }, { rel: "stylesheet", href: chartStyles }];

/** Every token a chart reads (design system §2.1 and §2.2). Charts never carry a hex literal. */
export const CHART_TOKENS = [
  "--ink", "--muted", "--line", "--field", "--fault",
  "--c-temp", "--c-vpd", "--c-rh", "--c-outdoor", "--c-band-wash", "--c-band-edge", "--c-setpoint", "--c-requested", "--c-bound", "--c-grid",
  "--eq-mister", "--eq-fog", "--eq-fan1", "--eq-fan2", "--eq-vent", "--eq-heat1", "--eq-heat2", "--eq-light-main", "--eq-light-grow",
  "--eq-water", "--eq-fert", "--eq-master",
] as const;

/** The token values on `:root`, read when a chart is built. */
export function readColors(): ChartColors {
  const style = getComputedStyle(document.documentElement);
  return Object.fromEntries(CHART_TOKENS.map((token) => [token, style.getPropertyValue(token).trim()]));
}

let loaded: typeof uPlot | null = null;
let loading: Promise<typeof uPlot> | null = null;
/**
 * The lazy import, shared by every chart on the page; its module is kept for `resetZoom`. A failed load is not cached,
 * and its rejection stays unhandled so chunk-reload.ts can recover a tab held across a release.
 */
export function loadUPlot(): Promise<typeof uPlot> {
  loading ??= import("uplot").then(
    (module) => (loaded = module.default),
    (error: unknown) => { loading = null; throw error; },
  );
  return loading;
}

/** Puts every chart synced on `key` (the lanes of #1127 included) back to the window. */
export function resetZoom(key: string, range: XRange) {
  if (!loaded) return;
  for (const plot of loaded.sync(key).plots) plot.setScale("x", { min: range.from, max: range.to });
}

export const FINE_POINTER = "(pointer: fine)";
const finePointer = () => typeof globalThis.matchMedia === "function" && globalThis.matchMedia(FINE_POINTER).matches;

/** True at 860 px and below; follows the viewport. */
export function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(narrowViewport);
  useEffect(() => {
    if (typeof globalThis.matchMedia !== "function") return;
    const query = globalThis.matchMedia(NARROW_QUERY);
    const change = () => setNarrow(query.matches);
    change();
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, []);
  return narrow;
}

const ids = new WeakMap<object, number>();
let lastId = 0;
const partKey = (part: unknown): string => {
  if (part === null || typeof part !== "object") return typeof part === "string" ? JSON.stringify(part) : String(part);
  let id = ids.get(part);
  if (id === undefined) { id = ++lastId; ids.set(part, id); }
  return `#${id}`;
};
/**
 * The same array while its elements are the same objects (or `parts` of them, such as a settings entry's history and
 * label), so a caller passing a fresh array literal on each render rebuilds no model, data or CSV.
 */
export function useStableArray<T>(items: readonly T[] | undefined, parts: (item: T) => readonly unknown[] = (item) => [item]): readonly T[] | undefined {
  const key = items === undefined ? "none" : items.map((item) => parts(item).map(partKey).join(":")).join(",");
  // The key holds every element's identity, so it is the whole dependency.
  return useMemo(() => items, [key]);
}

/** What a chart's options builder gets once uPlot has loaded. */
export type ChartEnv = {
  UPlot: typeof uPlot;
  colors: ChartColors;
  /** The device pixel ratio now (it changes with the browser zoom), for hook-drawn widths, dashes and fonts. */
  px: () => number;
  /** Drag-x zoom is on (a fine pointer). */
  fine: boolean;
  /** The x window uPlot is ranging or last ranged, zoom included, for a y range that follows it. */
  xWindow: () => [number, number];
};
export type ChartOptions = Omit<uPlot.Options, "width" | "height">;

export type UPlotChartProps = {
  /** Builds the series, y scale, axes and hooks; called when the chart is created. */
  options: (env: ChartEnv) => ChartOptions;
  data: uPlot.AlignedData;
  xRange: XRange;
  /** The chart is rebuilt when this changes (series, unit, sync key); data and marks only update it. */
  structure: string;
  /** `plot` (320 px, 220 px at 860 px and below) or `plot plot-small` (150 px), reserved in charts.css. */
  className: string;
  /** Names what is plotted and says a table follows. */
  label: string;
  syncKey?: string;
  /** Changes when hook-drawn marks change, so the chart re-ranges and repaints without new data. */
  marks?: unknown;
  onZoom?: (zoomed: boolean) => void;
  onPlot?: (plot: uPlot | null) => void;
};

function reservedHeight(element: HTMLElement, className: string): number {
  if (element.clientHeight > 0) return element.clientHeight;
  return className.includes("plot-small") ? 150 : narrowViewport() ? 220 : 320;
}

export function UPlotChart(props: UPlotChartProps) {
  const target = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const latest = useRef(props);
  latest.current = props;
  // The window the chart was last told to show; x differing from it is a zoom.
  const shown = useRef<XRange>(props.xRange);
  const zoomed = useRef(false);

  useEffect(() => {
    const element = target.current;
    if (!element) return;
    let disposed = false;
    let plot: uPlot | null = null;
    const observer = new ResizeObserver(() => plot?.setSize({ width: Math.max(1, element.clientWidth), height: reservedHeight(element, latest.current.className) }));
    observer.observe(element);
    void loadUPlot().then((UPlot) => {
      // A load that resolves after unmount is dropped.
      if (disposed) return;
      const { options, data, xRange, syncKey, className } = latest.current;
      shown.current = xRange;
      // A rebuilt chart starts on its window, unzoomed.
      if (zoomed.current) { zoomed.current = false; latest.current.onZoom?.(false); }
      let xWindow: [number, number] = [xRange.from, xRange.to];
      const fine = finePointer();
      const env: ChartEnv = { UPlot, colors: readColors(), px: () => UPlot.pxRatio || 1, fine, xWindow: () => xWindow };
      const built = options(env);
      const setScale = (self: uPlot, key: string) => {
        if (key !== "x") return;
        const now = self.scales.x.min !== shown.current.from || self.scales.x.max !== shown.current.to;
        if (now !== zoomed.current) { zoomed.current = now; latest.current.onZoom?.(now); }
      };
      plot = new UPlot({
        ...built,
        width: Math.max(1, element.clientWidth),
        height: reservedHeight(element, className),
        tzDate: (ts) => UPlot.tzDate(new Date(ts * 1000), TIME_ZONE),
        legend: { show: false },
        scales: {
          ...built.scales,
          // auto off: new data keeps the current x (a zoom survives a poll); the window is set explicitly.
          x: { time: true, auto: false, min: xRange.from, max: xRange.to, range: (_self, min, max) => { xWindow = [min, max]; return [min, max]; } },
        },
        cursor: {
          y: false, ...built.cursor,
          drag: { x: fine, y: false, setScale: fine },
          // uPlot's double-click would zoom out to the data's extent, not the window: "Reset zoom" does that.
          bind: { ...built.cursor?.bind, dblclick: () => null },
          ...(syncKey ? { sync: { key: syncKey, scales: ["x", null], setSeries: false } } : {}),
        },
        hooks: { ...built.hooks, setScale: [...(built.hooks?.setScale || []), setScale] },
      }, data, element);
      plotRef.current = plot;
      latest.current.onPlot?.(plot);
    });
    return () => {
      disposed = true;
      observer.disconnect();
      plot?.destroy();
      plotRef.current = null;
      latest.current.onPlot?.(null);
    };
  }, [props.structure]);

  // New data or marks for the same chart: setData keeps the zoom (x is not auto); an unzoomed chart follows the window.
  const { data, xRange, marks } = props;
  useEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    shown.current = xRange;
    plot.batch(() => {
      plot.setData(data);
      if (!zoomed.current) plot.setScale("x", { min: xRange.from, max: xRange.to });
    });
  }, [data, xRange.from, xRange.to, marks]);

  return <div ref={target} className={props.className} role="img" aria-label={props.label} />;
}
