// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import type uPlot from "uplot";
import { csvFilename, csvText } from "../charts/csv";
import { CsvLink, DataTable, TimeCell, type DataTableBlock } from "../charts/data-table";
import { Labels, labelColor, paintBandEdges, paintBandFill, paintNow, type PaintTarget } from "../charts/draw";
import { ChartKey, type KeyItem } from "../charts/legend";
import { marksExtent, paddedRange, type BandRun } from "../charts/marks";
import { valueAxis } from "../charts/time-series";
import { epochOf, fullTime, timeAxis } from "../charts/time-axis";
import { tooltipPlugin } from "../charts/tooltip";
import type { XRange } from "../charts/types";
import { UPlotChart, type ChartEnv, type ChartOptions } from "../charts/uplot-chart";
import { dateShift, formatValue, localDayStart, shortTime } from "../lib/format";
import { slotRead, useFreshRead, useGraphClock, useWindowReads } from "../lib/graph-reads";
import { ApiError, type BandPreview as BandPreviewData, type ScheduleAgenda, type Setting } from "../lib/operator-api";
import { unitLabel } from "../lib/setting-values";
import { problemMessage } from "../lib/writes";
import { Panel } from "./panel";

/**
 * The curve preview on a crop-band anchor or width's drawer (US-SET-07, screens SCR-05). Each change of the value field,
 * after 300 ms, reads API-19; the server overlays the draft on the `crop_band_anchors` rows `fn_crop_band_value` reads,
 * so the browser computes no curve. It is a read, never a write or a dry run: a 422 or a failure changes this panel and
 * nothing else, and Save stays the ordinary API-10. The day runs 00:00 to 24:00, the band as stored is the ribbon and a
 * solid target, the draft is dashed and labelled PREVIEW, with a now line and marks at sunrise, solar noon and sunset
 * from API-IRR-02 `solar[0]`, left off if that read fails.
 */

const ANCHOR = "(?:sr|sm|ss|mid)";
const ZONE = "(?:center|south|west|east)";
const BAND_ROW = new RegExp(`^(?:band_(temp|vpd)_(?:low|target|high)_${ANCHOR}|zone_vpd_target_(${ZONE})_${ANCHOR}|zone_vpd_width_(?:below|above)_(${ZONE}))$`);
/** `band_<series>_<anchor>`, `zone_vpd_target_<zone>_<anchor>` or a zone's width: the `crop_band` rows with a curve. */
export const hasBandCurve = (setting: Pick<Setting, "param" | "control_class">): boolean => setting.control_class === "crop_band" && BAND_ROW.test(setting.param);

/** What the panel's curve is: "House VPD", "Center zone VPD". */
function curveName(param: string): string {
  const [, axis, targetZone, widthZone] = BAND_ROW.exec(param) ?? [];
  const zone = targetZone ?? widthZone;
  if (zone) return `${zone[0].toUpperCase()}${zone.slice(1)} zone VPD`;
  return axis === "temp" ? "House temperature" : "House VPD";
}

export const PREVIEW_DEBOUNCE_MS = 300;
export const PREVIEW_UNAVAILABLE = "Preview unavailable";
export const PREVIEW_WORD = "PREVIEW";
const NUMBER_FIRST = "Enter a number to preview its curve";

/** `value` once it has held for `ms`; the first value at once. */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    if (Object.is(value, settled)) return;
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, settled, ms]);
  return settled;
}

type SolarMark = { at: number; word: string; text: string };
/** Sunrise, solar noon and sunset from `solar[0]`, only when it is the previewed day. */
function solarMarks(agenda: ScheduleAgenda | null, day: string): SolarMark[] {
  const solar = agenda?.solar[0];
  if (!solar || solar.date !== day) return [];
  const marks: [string, string, string][] = [["Sunrise", "Sunrise", solar.sunrise], ["Noon", "Solar noon", solar.solar_noon], ["Sunset", "Sunset", solar.sunset]];
  return marks.flatMap(([word, name, instant]) => {
    const at = epochOf(instant);
    return at === null ? [] : [{ at, word, text: `${name} ${shortTime(instant)}` }];
  });
}

/** One column of the API's curve: a null stays a null, so a gap stays a gap. */
type Column = readonly (number | null)[];
type Curve = BandPreviewData["current"];
/** The ribbon's runs: a null in low or high leaves it open. */
function ribbon(t: readonly number[], low: Column, high: Column): BandRun[] {
  const runs: BandRun[] = [];
  let run: BandRun | null = null;
  t.forEach((at, index) => {
    const bottom = low[index], top = high[index];
    if (bottom === null || bottom === undefined || top === null || top === undefined) { run = null; return; }
    if (!run) { run = { source: "band", label: "", t: [], low: [], high: [] }; runs.push(run); }
    run.t.push(at); run.low.push(bottom); run.high.push(top);
  });
  return runs;
}

type PreviewView = {
  unit: string;
  token: string;
  t: readonly number[];
  runs: BandRun[];
  current: Curve;
  /** Null when there is no draft to draw: none typed, or the draft's curve is the band as stored. */
  draft: Curve | null;
  now: number | null;
  marks: SolarMark[];
};

const sameColumn = (a: Column, b: Column) => a.length === b.length && a.every((value, index) => value === b[index]);
/** The draft to draw, or null when `draftShown` is off or the draft changes nothing. */
function draftOf(preview: BandPreviewData, draftShown: boolean): PreviewView["draft"] {
  const { current, draft } = preview;
  if (!draftShown || (sameColumn(current.low, draft.low) && sameColumn(current.target, draft.target) && sameColumn(current.high, draft.high))) return null;
  return draft;
}

function nulls(length: number): null[] {
  return Array.from({ length }, () => null);
}

/** Where PREVIEW sits: on the draft's line where it stands furthest from the band as stored, the target first (a width moves only its edge). */
function labelPoint(view: PreviewView): { at: number; value: number } | null {
  const { draft, current, t } = view;
  if (!draft) return null;
  let best: { at: number; value: number } | null = null, gap = -1;
  for (const key of ["target", "low", "high"] as const) {
    draft[key].forEach((value, index) => {
      const own = current[key][index];
      if (value === null || own === null || own === undefined) return;
      const apart = Math.abs(value - own);
      if (apart > gap) { gap = apart; best = { at: t[index], value }; }
    });
  }
  return best;
}

/** The now line, the solar marks and every label, which the collision check keeps apart; uPlot's context is left as found. */
function paintMarks(plot: PaintTarget, view: PreviewView, colors: ChartEnv["colors"], px: number) {
  const { ctx, bbox } = plot;
  const x = (at: number) => plot.valToPos(at, "x", true);
  const nowShown = view.now !== null && paintNow(plot, view.now, colors, px);
  ctx.save();
  try {
    ctx.strokeStyle = colors["--muted"];
    ctx.lineWidth = px;
    ctx.setLineDash([4 * px, 3 * px]);
    for (const mark of view.marks) {
      ctx.beginPath();
      ctx.moveTo(x(mark.at), bbox.top);
      ctx.lineTo(x(mark.at), bbox.top + bbox.height);
      ctx.stroke();
    }
    const labels = new Labels(plot, px);
    const top = bbox.top + 16 * px;
    const both = (at: number, line: number) => [{ at: x(at) + 4 * px, line, align: "left" as const }, { at: x(at) - 4 * px, line, align: "right" as const }];
    if (nowShown && view.now !== null) labels.place("Now", both(view.now, top), colors["--ink"]);
    for (const mark of view.marks) labels.place(mark.word, both(mark.at, top), colors["--muted"]);
    const point = labelPoint(view);
    if (point) labels.place(PREVIEW_WORD, both(point.at, plot.valToPos(point.value, "y", true)), labelColor(colors["--c-requested"], colors["--warn-text"]));
  } finally {
    ctx.restore();
  }
}

/** A column's low, target and high at one point, in words. */
function pointText(unit: string, low: number | null | undefined, target: number | null | undefined, high: number | null | undefined): string {
  return `${formatValue(low, unit)}–${formatValue(high, unit)} · target ${formatValue(target, unit)}`;
}

function previewOptions(env: ChartEnv, viewRef: MutableRefObject<PreviewView>): ChartOptions {
  const { colors, px } = env;
  const { unit, token } = viewRef.current;
  const draftLine = (label: string, width: number): uPlot.Series => ({ label, scale: "y", stroke: colors["--c-requested"], width, dash: [8, 4], spanGaps: false, points: { show: false } });
  return {
    padding: [18, 10, 0, 0],
    series: [{}, { label: "Target", scale: "y", stroke: colors[token], width: 2, spanGaps: false, points: { show: false } }, draftLine(`${PREVIEW_WORD} target`, 2), draftLine(`${PREVIEW_WORD} low`, 1), draftLine(`${PREVIEW_WORD} high`, 1)],
    scales: { y: { range: (_plot, min, max) => paddedRange([min, max], marksExtent(viewRef.current.runs, [], ...env.xWindow())) } },
    axes: [timeAxis({ muted: colors["--muted"], grid: colors["--c-grid"] }), valueAxis(unit, colors)],
    hooks: {
      drawClear: [(plot) => paintBandFill(plot, viewRef.current.runs, colors)],
      drawAxes: [(plot) => paintBandEdges(plot, viewRef.current.runs, colors, px())],
      draw: [(plot) => paintMarks(plot, viewRef.current, colors, px())],
    },
    plugins: [tooltipPlugin((plot) => {
      const index = plot.cursor.idx;
      const { t, unit: shown, current, draft } = viewRef.current;
      if (index === null || index === undefined || index < 0 || index >= t.length) return null;
      const lines = [fullTime(t[index]), `Band ${pointText(shown, current.low[index], current.target[index], current.high[index])}`];
      if (draft) lines.push(`${PREVIEW_WORD} ${pointText(shown, draft.low[index], draft.target[index], draft.high[index])}`);
      return lines;
    })],
  };
}

const CURVE_KEYS = ["low", "target", "high"] as const;
/** The table lists each point of the band and, while one shows, of the draft; rows build only once it opens. */
function previewTable({ t, unit, current, draft }: PreviewView): DataTableBlock {
  const headers = ["Time", ...CURVE_KEYS.map((key) => `${key} (${unit})`), ...(draft ? CURVE_KEYS.map((key) => `${PREVIEW_WORD} ${key} (${unit})`) : [])];
  return {
    caption: "Band preview · America/Denver", headers, count: t.length,
    rows: () => t.map((at, index) => [
      <TimeCell key="t" at={at} />,
      ...CURVE_KEYS.map((key) => formatValue(current[key][index], unit)),
      ...(draft ? CURVE_KEYS.map((key) => formatValue(draft[key][index], unit)) : []),
    ]),
  };
}
/** The CSV: exactly the plotted columns, in canonical units. */
function previewCsv({ t, unit, current, draft }: PreviewView): string {
  return csvText({
    t,
    columns: [
      ...CURVE_KEYS.map((key) => ({ header: `${key} (${unit})`, values: current[key] })),
      ...(draft ? CURVE_KEYS.map((key) => ({ header: `preview ${key} (${unit})`, values: draft[key] })) : []),
    ],
  });
}

/** The day's curve, once the API has answered: 00:00 to 24:00 local, which a DST day makes 23 or 25 hours. */
function PreviewChart({ day, view, name }: { day: string; view: PreviewView; name: string }) {
  const viewRef = useRef(view);
  viewRef.current = view;
  const xRange: XRange = useMemo(() => {
    const from = epochOf(localDayStart(day)) ?? view.t[0] ?? 0;
    return { from, to: epochOf(localDayStart(dateShift(day, 1))) ?? from + 86_400 };
  }, [day, view.t]);
  const data = useMemo<uPlot.AlignedData>(() => {
    const blank = nulls(view.t.length);
    return [[...view.t], [...view.current.target], [...(view.draft?.target ?? blank)], [...(view.draft?.low ?? blank)], [...(view.draft?.high ?? blank)]];
  }, [view.t, view.current, view.draft]);
  // The marks repaint the same chart; a new unit rebuilds it, with its axis and colours.
  const marks = useMemo(() => ({ runs: view.runs, draft: view.draft, now: view.now, solar: view.marks }), [view.runs, view.draft, view.now, view.marks]);
  return <>
    <UPlotChart options={(env) => previewOptions(env, viewRef)} data={data} xRange={xRange} structure={`band-preview|${view.unit}`} className="plot" zoom={false} marks={marks}
      label={`${name} band over the day in ${view.unit}, America/Denver: the band as stored${view.draft ? " and dashed, the preview of this value" : ""}. Gaps are left empty, never zero. The data table follows.`} />
    <ChartKey items={previewKey(view)} />
    {view.marks.length > 0 && <ul className="chart-notes">{view.marks.map((mark) => <li key={mark.word}>{mark.text}</li>)}</ul>}
    <div className="chart-tools"><CsvLink filename={csvFilename(`band preview ${name}`, xRange)} build={() => previewCsv(view)} version={data} /></div>
    <DataTable tables={[previewTable(view)]} />
  </>;
}

function previewKey(view: PreviewView): KeyItem[] {
  return [
    { key: "band", label: "Band as stored", token: "--c-band-edge", kind: "fill" },
    { key: "target", label: "Target as stored", token: view.token, kind: "line" },
    ...(view.draft ? [{ key: "preview", label: PREVIEW_WORD, token: "--c-requested", kind: "dashed" } as const] : []),
  ];
}

/** Today's solar times, read once; a failure leaves the marks off. */
const SOLAR_SLOTS = [{ id: "solar", params: { days: 1 } }] as const;

export type BandPreviewProps = {
  setting: Pick<Setting, "param" | "unit" | "step" | "control_class">;
  /** The value field as a number; null while it does not read as one. */
  draft: number | null;
};

export function BandPreviewPanel({ setting, draft }: BandPreviewProps) {
  const settled = useDebounced(draft, PREVIEW_DEBOUNCE_MS);
  const read = useFreshRead("API-19", settled === null ? null : { param: setting.param, value: settled });
  const solar = slotRead(useWindowReads("API-IRR-02", SOLAR_SLOTS, null), "solar");
  const now = useGraphClock(true);
  const preview = read.data ?? null;
  const unit = preview ? unitLabel(preview.unit) : unitLabel(setting.unit);
  const name = curveName(setting.param);
  // While the next answer loads the last one stays on screen, its draft too; with no number in the field there is no draft.
  const draftShown = settled !== null;
  const view = useMemo<PreviewView | null>(() => (preview ? {
    unit, token: unit === "°F" ? "--c-temp" : "--c-vpd", t: preview.t, runs: ribbon(preview.t, preview.current.low, preview.current.high), current: preview.current,
    draft: draftOf(preview, draftShown), now, marks: solarMarks(solar.data, preview.day),
  } : null), [preview, unit, draftShown, now, solar.data]);
  let message: string | null = null;
  if (read.isError) {
    const { error } = read;
    message = error instanceof ApiError && error.status === 422 ? problemMessage(error.problem, { unit: setting.unit, step: setting.step }) : PREVIEW_UNAVAILABLE;
  } else if (settled === null && !preview) {
    message = NUMBER_FIRST;
  } else if (preview && preview.t.length === 0) {
    message = PREVIEW_UNAVAILABLE;
  }
  return <div className="band-preview">
    <Panel title="Band preview" headingLevel={3} thing="the band preview" state={message !== null || preview ? "ready" : "loading"} reserve="lg">
      <figure className="chart">
        <figcaption className="eyebrow">{name} · {unit} · America/Denver</figcaption>
        {message !== null || !preview || !view
          ? <div className="plot chart-message"><p role="status">{message}</p></div>
          : <PreviewChart day={preview.day} view={view} name={name} />}
      </figure>
    </Panel>
  </div>;
}
