// SPDX-License-Identifier: MIT
import { useEffect, useMemo, useState } from "react";
import { clockTime } from "../../charts/time-axis";
import { formatValue } from "../../lib/format";
import { useFreshRead, useGraphClock } from "../../lib/graph-reads";
import { ApiError, type BandProfile, type BandProfilePreview, type ParamsOf } from "../../lib/operator-api";
import { parseDecimal } from "../../lib/setting-values";
import { OperatorOnly } from "../../lib/session";
import { errorFor, formError, problemMessage, useBandWrite, MANUAL_DEFAULT } from "../../lib/writes";
import { Button } from "../button";
import { ManualToggle } from "../forms/manual-toggle";
import { NumberField } from "../forms/number-field";
import { Reason } from "../forms/reason";
import { Panel } from "../panel";
import { PREVIEW_DEBOUNCE_MS, PREVIEW_UNAVAILABLE, PreviewChart, draftOf, ribbon, type ExtraLine, type PreviewView } from "../band-preview";

/**
 * The band editor (Control → Band, #1334): the house band in the operator's terms, with a live 24 h preview. The
 * fields are the simple profile (day and night targets, the temperature ± width and the VPD range below and above); a
 * field changed recompiles its axis with sunrise and sunset at the midpoint of noon and midnight, so the curve never
 * leaves the values typed. Advanced shows the 24 raw anchors. Every edit, after 300 ms, reads API-23, which evaluates
 * the draft with the controller's own curve, so the browser computes no curve. Save is one API-24 write of every
 * changed anchor with one reason and one Activity row; the dispatcher delivers it, never the page.
 */

export const PROFILE_FIELDS = [
  "temp_day", "temp_night", "temp_width_day", "temp_width_night", "vpd_day", "vpd_night", "vpd_below_day", "vpd_above_day", "vpd_below_night", "vpd_above_night",
] as const;
export type ProfileField = (typeof PROFILE_FIELDS)[number];
const SERIES = ["temp_low", "temp_target", "temp_high", "vpd_low", "vpd_target", "vpd_high"] as const;
const ANCHORS = [["sr", "Sunrise"], ["sm", "Solar noon"], ["ss", "Sunset"], ["mid", "Solar midnight"]] as const;
const ANCHOR_PARAMS = SERIES.flatMap((series) => ANCHORS.map(([anchor]) => `band_${series}_${anchor}`));

const FIELD_LABEL: Record<ProfileField, string> = {
  temp_day: "Day target (solar noon)", temp_night: "Night target (solar midnight)", temp_width_day: "Day width ±", temp_width_night: "Night width ±",
  vpd_day: "Day target (solar noon)", vpd_night: "Night target (solar midnight)", vpd_below_day: "Day range below", vpd_above_day: "Day range above",
  vpd_below_night: "Night range below", vpd_above_night: "Night range above",
};
const unitOf = (name: string) => (name.includes("temp") ? "°F" : "kPa");
const stepOf = (name: string) => (name.includes("temp") ? 0.1 : 0.01);
const SERIES_LABEL: Record<(typeof SERIES)[number], string> = {
  temp_low: "Temp low", temp_target: "Temp target", temp_high: "Temp high", vpd_low: "VPD low", vpd_target: "VPD target", vpd_high: "VPD high",
};

type Texts = Readonly<Record<string, string>>;
const textOf = (value: number, name: string) => value.toFixed(name.includes("temp") ? 1 : 2);
/** The fields as text from a profile, and the anchors from an anchor map. */
export function profileTexts(profile: BandProfile): Texts {
  return Object.fromEntries(PROFILE_FIELDS.map((name) => [name, textOf(profile[name], name)]));
}
function anchorTexts(anchors: Readonly<Record<string, number>>): Texts {
  return Object.fromEntries(ANCHOR_PARAMS.map((name) => [name, anchors[name] === undefined ? "" : textOf(anchors[name], name)]));
}

/**
 * API-23's query from the fields the operator changed: a profile field that parses and differs from the band's own
 * value, and each raw anchor that does; null while a changed field does not read as a number.
 */
export function draftParams(texts: Texts, base: Texts, anchors: Texts, anchorBase: Texts): ParamsOf<"API-23"> | null {
  const params: Record<string, number | string[]> = {};
  for (const name of PROFILE_FIELDS) {
    if (texts[name] === base[name]) continue;
    const value = parseDecimal(texts[name] ?? "");
    if (value === null) return null;
    params[name] = value;
  }
  const raw: string[] = [];
  for (const name of ANCHOR_PARAMS) {
    if (anchors[name] === anchorBase[name]) continue;
    const value = parseDecimal(anchors[name] ?? "");
    if (value === null) return null;
    raw.push(`${name}:${value}`);
  }
  if (raw.length) params.anchor = raw;
  return params as ParamsOf<"API-23">;
}

const FORECAST_STALE_S = 3 * 3600;
const shown = (values: readonly (number | null)[]) => values.some((value) => value !== null);
/**
 * The measured last 24 h (solid, in the metric's colour as on Graphs) and the outdoor forecast (dashed, `--c-outdoor`).
 * API-23 places the measured values by clock time, so to the right of the now line they are yesterday's.
 */
export function overlays(preview: Pick<BandProfilePreview, "measured" | "forecast">, axis: "temp" | "vpd", now: number | null): ExtraLine[] {
  const out: ExtraLine[] = [];
  const measured = preview.measured[axis];
  if (shown(measured)) out.push({ key: "measured", label: "Measured, last 24 h (after now: yesterday)", token: axis === "temp" ? "--c-temp" : "--c-vpd", values: measured, kind: "line" });
  const forecast = preview.forecast[axis];
  const issued = preview.forecast.issued_at === null ? null : Date.parse(preview.forecast.issued_at) / 1000;
  // As on Graphs (#1354): a forecast issued more than 3 h ago reads STALE.
  const stale = issued !== null && now !== null && now - issued > FORECAST_STALE_S;
  if (shown(forecast)) out.push({ key: "forecast", label: stale ? "Outdoor forecast (STALE)" : "Outdoor forecast", token: "--c-outdoor", values: forecast, kind: "dashed" });
  return out;
}

/** The day's two charts as the band preview draws them, the VPD one with the controller's night bias and the achievable night VPD (#1433). */
function views(preview: BandProfilePreview, now: number | null, edited: boolean): { temp: PreviewView; vpd: PreviewView } {
  const marks = [["Sunrise", preview.sun.sunrise], ["Noon", preview.sun.solar_noon], ["Sunset", preview.sun.sunset]]
    .flatMap(([word, at]) => (typeof at === "number" ? [{ at, word: word as string, text: `${word === "Noon" ? "Solar noon" : word} ${clockTime(at)}` }] : []));
  const extras: ExtraLine[] = [];
  if ((preview.night_vpd_bias_kpa ?? 0) > 0) extras.push({ key: "device", label: "As the controller runs it (night bias)", token: "--c-setpoint", values: preview.vpd.device.target });
  if (preview.vpd.expected_low.some((value) => value !== null)) {
    extras.push({ key: "expected-low", label: "Achievable night VPD (p25)", token: "--c-outdoor", values: preview.vpd.expected_low });
    extras.push({ key: "expected-high", label: "Achievable night VPD (p75)", token: "--c-outdoor", values: preview.vpd.expected_high });
  }
  // The target as stored takes the band's edge colour, so the measured trace keeps the metric's own, as on Graphs.
  const axis = (curves: BandProfilePreview["temp"], unit: string, more: readonly ExtraLine[]): PreviewView => ({
    unit, token: "--c-band-edge", t: preview.t, runs: ribbon(preview.t, curves.current.low, curves.current.high), current: curves.current, draft: draftOf(curves, edited), now, marks, extras: more,
  });
  return { temp: axis(preview.temp, "°F", overlays(preview, "temp", now)), vpd: axis(preview.vpd, "kPa", [...extras, ...overlays(preview, "vpd", now)]) };
}

export function BandEditor() {
  const base = useFreshRead("API-23", {});
  const current = base.data ?? null;
  const baseTexts = useMemo(() => (current ? profileTexts(current.current.profile) : {}), [current]);
  const baseAnchors = useMemo(() => (current ? anchorTexts(current.current.anchors) : {}), [current]);
  const [texts, setTexts] = useState<Texts | null>(null);
  const [anchors, setAnchors] = useState<Texts | null>(null);
  const [splitWidth, setSplitWidth] = useState(false);
  const [manual, setManual] = useState(MANUAL_DEFAULT);
  const [reason, setReason] = useState("");
  const write = useBandWrite();
  // The fields start from the band as stored, and again once the read after a Save brings the band it stored.
  const [seed, setSeed] = useState<BandProfilePreview | null>(null);
  const [resync, setResync] = useState(false);
  useEffect(() => {
    if (!current || (seed !== null && !(resync && current !== seed))) return;
    setTexts(baseTexts);
    setAnchors(baseAnchors);
    setSplitWidth(current.current.profile.temp_width_day !== current.current.profile.temp_width_night);
    setSeed(current);
    setResync(false);
  }, [current, seed, resync, baseTexts, baseAnchors]);
  const fields = texts ?? baseTexts;
  const rawAnchors = anchors ?? baseAnchors;
  const params = useMemo(() => (current ? draftParams(fields, baseTexts, rawAnchors, baseAnchors) : null), [current, fields, baseTexts, rawAnchors, baseAnchors]);
  const [settled, setSettled] = useState(params);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(params), PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [params]);
  const edited = settled !== null && Object.keys(settled).length > 0;
  const read = useFreshRead("API-23", edited ? settled : null);
  const preview = edited ? read.data ?? current : current;
  const now = useGraphClock(true);
  const charts = useMemo(() => (preview ? views(preview, now, edited) : null), [preview, now, edited]);
  const setField = (name: string, value: string) => {
    write.clearError();
    setTexts((before) => {
      const next = { ...(before ?? baseTexts), [name]: value };
      return !splitWidth && name === "temp_width_day" ? { ...next, temp_width_night: value } : next;
    });
  };
  const setAnchor = (name: string, value: string) => {
    write.clearError();
    setAnchors((before) => ({ ...(before ?? baseAnchors), [name]: value }));
  };
  const errors = preview?.findings.filter((finding) => finding.severity === "error") ?? [];
  const warnings = preview?.findings.filter((finding) => finding.severity === "warning") ?? [];
  const changed = edited ? preview?.changed ?? [] : [];
  const save = async () => {
    if (!params) return;
    const { anchor, ...profile } = params as Record<string, unknown>;
    const raw = Object.fromEntries(((anchor as string[] | undefined) ?? []).map((item) => {
      const [name, value] = item.split(":");
      return [name, Number(value)];
    }));
    const outcome = await write.save({ profile: profile as Record<string, number>, anchors: raw, manual, ...(reason ? { reason } : {}) });
    if (outcome.ok) {
      setResync(true);
      setReason("");
    }
  };
  let message: string | null = null;
  if (base.isError) message = PREVIEW_UNAVAILABLE;
  else if (read.isError && edited) {
    const { error } = read;
    message = error instanceof ApiError && error.status === 422 ? problemMessage(error.problem) : PREVIEW_UNAVAILABLE;
  } else if (params === null && current) message = "Enter a number in every changed field to preview the band";
  const fieldNames = [...PROFILE_FIELDS, ...ANCHOR_PARAMS, "reason"];
  const field = (name: ProfileField) => <NumberField key={name} label={FIELD_LABEL[name]} value={fields[name] ?? ""} onChange={(value) => setField(name, value)}
    unit={unitOf(name)} step={stepOf(name)} min={name.includes("width") || name.includes("below") || name.includes("above") ? 0 : null}
    error={errorFor(write.error, name) ?? (errors.find((finding) => finding.field === name)?.detail ?? null)} />;
  return <>
    <Panel title="Band preview" thing="the band" state={current || base.isError ? "ready" : "loading"} reserve="lg"
      foot={preview ? <BandFacts preview={preview} /> : null}>
      {message !== null && <p role="status" className="chart-message">{message}</p>}
      {charts && preview && <div className="ctl-band-charts">
        <figure className="chart"><figcaption className="eyebrow">House temperature · °F</figcaption><PreviewChart day={preview.day} view={charts.temp} name="House temperature" /></figure>
        <figure className="chart"><figcaption className="eyebrow">House VPD · kPa</figcaption><PreviewChart day={preview.day} view={charts.vpd} name="House VPD" /></figure>
      </div>}
      {(errors.length > 0 || warnings.length > 0) && <ul className="ctl-band-findings">
        {errors.map((finding) => <li key={`${finding.code}-${finding.field}`} className="fault">{finding.detail}</li>)}
        {warnings.map((finding) => <li key={`${finding.code}-${finding.field}`} className="ctl-band-warn">{finding.detail}</li>)}
      </ul>}
    </Panel>
    <OperatorOnly>
      {current && <Panel title="Set the band" thing="the band" state="ready">
        <form className="ctl-band-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
          <fieldset>
            <legend>Temperature</legend>
            {field("temp_day")}
            {field("temp_night")}
            {field("temp_width_day")}
            <label className="check field-check"><input type="checkbox" checked={splitWidth} onChange={(event) => setSplitWidth(event.target.checked)} /><span>Separate night width</span></label>
            {splitWidth && field("temp_width_night")}
          </fieldset>
          <fieldset>
            <legend>VPD</legend>
            {field("vpd_day")}
            {field("vpd_night")}
            {field("vpd_below_day")}
            {field("vpd_above_day")}
            {field("vpd_below_night")}
            {field("vpd_above_night")}
          </fieldset>
          <details className="ctl-band-advanced">
            <summary>Advanced: the 24 anchors</summary>
            <p className="meta">Sunrise and sunset that are not the midpoint of noon and midnight make the curve run past the values set here; the preview shows how far.</p>
            <table className="table">
              <thead><tr><th scope="col">Series</th>{ANCHORS.map(([anchor, label]) => <th key={anchor} scope="col">{label}</th>)}</tr></thead>
              <tbody>{SERIES.map((series) => <tr key={series}>
                <th scope="row">{SERIES_LABEL[series]} ({unitOf(series)})</th>
                {ANCHORS.map(([anchor, label]) => {
                  const name = `band_${series}_${anchor}`;
                  return <td key={anchor}><input type="text" inputMode="decimal" aria-label={`${SERIES_LABEL[series]} at ${label.toLowerCase()}`} value={rawAnchors[name] ?? ""}
                    onChange={(event) => setAnchor(name, event.target.value)} /></td>;
                })}
              </tr>)}</tbody>
            </table>
          </details>
          <Reason value={reason} onChange={setReason} error={errorFor(write.error, "reason")} />
          <ManualToggle checked={manual} onChange={setManual} />
          {formError(write.error, fieldNames) && <p className="fault" role="alert">{formError(write.error, fieldNames)}</p>}
          <p className="meta" role="status">{changed.length === 0 ? "No change yet" : `Save writes ${changed.length} ${changed.length === 1 ? "anchor" : "anchors"} in one change`}</p>
          <Button type="submit" variant="primary" write busy={write.sending} disabled={!params || changed.length === 0 || errors.length > 0}>Save band</Button>
        </form>
      </Panel>}
    </OperatorOnly>
  </>;
}

/** The guardrails, the night bias, the night heat estimate and the expected-VPD fit, in words. */
function BandFacts({ preview }: { preview: BandProfilePreview }) {
  const { guardrails: g, night_heat_kwh: heat, envelope } = preview;
  const kwh = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `${value.toFixed(1)} kWh`);
  return <ul className="meta ctl-band-facts">
    <li>Guardrails {formatValue(g.temp_min_f, "°F")} and {formatValue(g.temp_max_f, "°F")}; the band stays {formatValue(g.margin_f, "°F")} inside them. VPD {g.vpd_min_kpa.toFixed(2)}–{g.vpd_max_kpa.toFixed(2)} kPa; the night floor stays at least {g.night_vpd_floor_kpa.toFixed(2)} kPa so the house dries out overnight.</li>
    <li>Night dry-out bias (planner): {preview.night_vpd_bias_kpa === null ? "no readback" : `${preview.night_vpd_bias_kpa.toFixed(2)} kPa at solar midnight`}.</li>
    <li>Night heat estimate: {kwh(heat.current)} as stored, {kwh(heat.draft)} with this draft (0.19 kW per °F above the forecast).</li>
    <li>{envelope ? `Achievable night VPD (what the house is expected to reach at this target): fitted on ${envelope.hours} quiet night hours of the last ${envelope.days} days (±${envelope.rmse_f.toFixed(1)} °F dewpoint). A night floor above it warns.` : "Achievable night VPD: not enough quiet night hours to fit."}</li>
  </ul>;
}
