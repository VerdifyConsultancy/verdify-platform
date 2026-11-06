// SPDX-License-Identifier: MIT
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { compressLabels, cropName, countText, daysIn, daysText, endReasonText, eventText, STAGES, stageText } from "../../lib/crops";
import { localDate, localDayStart, shortDateTime } from "../../lib/format";
import type { CropCatalog, Locations, PlantingDetail, PlantingList } from "../../lib/operator-api";
import { useApiRead } from "../../lib/queries";
import { useRole, useWritesEnabled } from "../../lib/session";
import { errorFor, formError, useCropWrite } from "../../lib/writes";
import { Button } from "../button";
import { EditDrawer, FieldError, FieldHint, NumberField, Reason, Select, Switch, describedBy, useFieldIds } from "../forms";
import { firstField, focusables, useModalDialog } from "../forms/modal";
import { useClockValue } from "../freshness";
import { PanelError } from "../panel";
import { useToast } from "../toast";
import { usePlantingHref } from "./map";

const PREVIEW_MS = 60_000;
const minute = (now: number) => Math.floor(now / 60_000) * 60_000;
const today = (now: number) => localDate(new Date(now).toISOString());
/** A whole number from a field, null when empty; NaN when it is not one. */
const wholeNumber = (text: string): number | null => (text.trim() === "" ? null : /^\d+$/.test(text.trim()) ? Number(text.trim()) : Number.NaN);
/** A date field's value as the API takes it: the day itself (local midnight), or nothing for today, which means now. */
const dayOrNow = (day: string, now: number) => (day && day !== today(now) ? day : undefined);

// ---------------------------------------------------------------------------------------------------- fields
function TextField({ label, value, onChange, hint, error, list, maxLength = 200, required = false }: {
  label: ReactNode; value: string; onChange: (text: string) => void; hint?: ReactNode; error?: string | null; list?: readonly string[]; maxLength?: number; required?: boolean;
}) {
  const ids = useFieldIds();
  const listId = `${ids.id}-list`;
  return <div className={error ? "field field-invalid" : "field"}>
    <label htmlFor={ids.id}>{label}</label>
    <input id={ids.id} type="text" value={value} maxLength={maxLength} autoComplete="off" list={list?.length ? listId : undefined} aria-required={required || undefined}
      aria-invalid={error ? true : undefined} aria-describedby={describedBy(hint ? ids.hintId : null, error ? ids.errorId : null)} onChange={(event) => onChange(event.target.value)} />
    {list && list.length > 0 && <datalist id={listId}>{list.map((item) => <option key={item} value={item} />)}</datalist>}
    {hint && <FieldHint id={ids.hintId}>{hint}</FieldHint>}
    {error && <FieldError id={ids.errorId}>{error}</FieldError>}
  </div>;
}
function DateField({ label, value, onChange, max, error }: { label: ReactNode; value: string; onChange: (day: string) => void; max: string; error?: string | null }) {
  const ids = useFieldIds();
  return <div className={error ? "field field-invalid" : "field"}>
    <label htmlFor={ids.id}>{label}</label>
    <input id={ids.id} type="date" value={value} max={max} aria-invalid={error ? true : undefined} aria-describedby={describedBy(ids.hintId, error ? ids.errorId : null)}
      onChange={(event) => onChange(event.target.value)} />
    <FieldHint id={ids.hintId}>America/Denver; today means now</FieldHint>
    {error && <FieldError id={ids.errorId}>{error}</FieldError>}
  </div>;
}

/**
 * What a place text means before Save (API-CROP-06, read-only, so viewers and a writes-off session preview too): the
 * resolved places and any planting that holds one of those cells at the date, which Save would refuse unless Replace
 * is on. `planting` leaves a move's own cells out.
 */
export function PlacesPreview({ q, at, planting, label = "Places" }: { q: string; at?: string; planting?: number; label?: string }) {
  const settled = useDebounced(q.trim(), 400);
  return settled ? <Preview q={settled} at={at} planting={planting} label={label} /> : null;
}
/** `value` once it has stopped changing for `ms`, so typing a place reads it once, not per key. */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}
function Preview({ q, at, planting, label }: { q: string; at?: string; planting?: number; label: string }) {
  const params = { q, ...(at ? { at: localDayStart(at) } : {}), ...(planting ? { planting } : {}) };
  const read = useApiRead("API-CROP-06", params, { intervalMs: PREVIEW_MS });
  const resolution = read.data;
  const conflicts = resolution?.conflicts ?? [];
  if (read.error && !resolution) {
    const problem = (read.error as { problem?: { code?: string; detail?: string } }).problem;
    return <div className="crop-preview" role="status"><p className="field-error">{problem?.detail ?? "Couldn't read those places"}</p></div>;
  }
  if (!resolution) return <div className="crop-preview" role="status"><p className="meta">Reading the places…</p></div>;
  const cells = resolution.cells.length;
  return <div className="crop-preview" role="status">
    <p><span className="field-label">{label}:</span> {resolution.where || "none"}{cells > 0 && <span className="meta"> ({cells} cell{cells === 1 ? "" : "s"})</span>}</p>
    {conflicts.length > 0 && <div className="crop-conflicts">
      <p className="crop-conflict-head">Already planted:</p>
      <ul>{[...new Set(conflicts.map((conflict) => conflict.planting_id))].map((id) => {
        const held = conflicts.filter((conflict) => conflict.planting_id === id);
        return <li key={id}>{compressLabels(held.map((conflict) => conflict.cell)).join(", ")}: {held[0].crop} (planting {id})</li>;
      })}</ul>
    </div>}
  </div>;
}

// ---------------------------------------------------------------------------------------------------- plant
/** A crop and variety kept between plantings in re-plant mode, or picked from a past planting ("Plant again"). */
export type PlantTemplate = { readonly crop: string; readonly variety: string; readonly perCell: string; readonly day: string };

/**
 * Plant… (API-CROP-07): the crop (the catalog's names and recent crops offered), its variety (recent ones offered),
 * plants per cell, the total (blank is unknown), the planted date (today by default; backdating allowed) and notes,
 * with the places resolved and any planting already there shown before Save. Replace ends or trims what is there.
 * "Keep for the next selection" is re-plant mode: the crop, variety and date stay, so each next selection is
 * Plant… and Save.
 */
export function PlantDrawer({ positions, cellCount, template, onClose, onSaved }: {
  positions: readonly string[]; cellCount: number; template: PlantTemplate | null; onClose: () => void; onSaved: (kept: PlantTemplate | null) => void;
}) {
  const now = useClockValue(minute);
  const toast = useToast();
  const catalog = useApiRead("API-CROP-05", {}, { intervalMs: 300_000 });
  const recent = useApiRead("API-CROP-02", { active: false, limit: 100 }, { intervalMs: 300_000 });
  const [crop, setCrop] = useState(template?.crop ?? "");
  const [variety, setVariety] = useState(template?.variety ?? "");
  const [perCell, setPerCell] = useState(template?.perCell ?? "1");
  const [total, setTotal] = useState("");
  const [day, setDay] = useState(template?.day ?? today(now));
  const [notes, setNotes] = useState("");
  const [replace, setReplace] = useState(false);
  const [keep, setKeep] = useState(template !== null);
  const [typed, setTyped] = useState<{ field: string; message: string } | null>(null);
  const write = useCropWrite("API-CROP-07");
  const names = useMemo(() => cropNames(catalog.data ?? null, recent.data ?? null), [catalog.data, recent.data]);
  const varieties = useMemo(() => varietiesOf(recent.data ?? null, crop), [recent.data, crop]);
  const each = wholeNumber(perCell);
  const shownTotal = total !== "" ? total : cellCount > 0 && each !== null && Number.isFinite(each) ? String(cellCount * each) : "";
  const save = async () => {
    const plants = wholeNumber(shownTotal);
    const problem = !crop.trim() ? { field: "crop", message: "Name the crop" }
      : cellCount > 0 && (each === null || Number.isNaN(each)) ? { field: "count_per_cell", message: "A whole number" }
      : plants !== null && Number.isNaN(plants) ? { field: "count", message: "A whole number, or blank for unknown" }
      : day > today(now) ? { field: "planted_at", message: "Pick today or a day before" } : null;
    setTyped(problem);
    if (problem) return;
    const outcome = await write.send({}, {
      crop: crop.trim(), positions: [...positions], ...(variety.trim() ? { variety: variety.trim() } : {}), ...(cellCount > 0 && each !== null ? { count_per_cell: each } : {}),
      ...(plants !== null ? { count: plants } : {}), ...(dayOrNow(day, now) ? { planted_at: day } : {}), ...(notes.trim() ? { notes: notes.trim() } : {}), ...(replace ? { replace: true } : {}),
    });
    if (!outcome.ok) return;
    toast.show({ message: `Planted ${cropName(outcome.data)} at ${outcome.data.where ?? positions.join(", ")}` });
    onSaved(keep ? { crop: crop.trim(), variety: variety.trim(), perCell, day } : null);
  };
  const error = (field: string) => (typed?.field === field ? typed.message : errorFor(write.error, field));
  return <EditDrawer open onClose={onClose} title="Plant" heading={positions.join(", ")} onSave={() => void save()} saving={write.sending} saveLabel="Save planting"
    error={formError(write.error, ["crop", "variety", "count", "count_per_cell", "planted_at", "notes"])}
    status={<PlacesPreview q={positions.join(", ")} at={dayOrNow(day, now)} />}>
    <TextField label="Crop" value={crop} onChange={setCrop} list={names} required error={error("crop")} hint="A catalog crop (Lettuce, Basil) or any name" />
    <TextField label="Variety (optional)" value={variety} onChange={setVariety} list={varieties} error={error("variety")} />
    {cellCount > 0 && <NumberField label="Plants per cell" value={perCell} onChange={setPerCell} min={0} max={1000} step={1} error={error("count_per_cell")} />}
    <NumberField label="Plants in all (optional)" value={shownTotal} onChange={setTotal} min={0} max={100000} step={1} error={error("count")}
      hint={cellCount > 0 ? `${cellCount} cell${cellCount === 1 ? "" : "s"} × per cell, unless you change it; blank is unknown` : "Blank is unknown"} />
    <DateField label="Planted" value={day} onChange={setDay} max={today(now)} error={error("planted_at")} />
    <Reason label="Notes (optional)" value={notes} onChange={setNotes} maxLength={2000} error={error("notes")} />
    <Switch label="Replace what is planted there" checked={replace} onChange={setReplace} />
    <Switch label="Keep this crop and date for the next selection" checked={keep} onChange={setKeep} />
  </EditDrawer>;
}
/** The catalog's names, then recent crops not in it, each once. */
function cropNames(catalog: CropCatalog | null, recent: PlantingList | null): string[] {
  return [...new Set([...(catalog?.items ?? []).map((item) => item.name), ...(recent?.items ?? []).map((item) => item.crop)])];
}
/** The varieties recorded for `crop` (any crop when blank), newest first. */
function varietiesOf(recent: PlantingList | null, crop: string): string[] {
  const key = crop.trim().toLowerCase();
  return [...new Set((recent?.items ?? []).filter((item) => !key || item.crop.toLowerCase() === key || item.crop_catalog_slug === key).map((item) => item.variety).filter((value): value is string => Boolean(value)))];
}

// ---------------------------------------------------------------------------------------------------- a drawer to read
/** A drawer that reads (the cell's and the planting's), with Done and the actions an operator gets. */
function ReadDrawer({ title, heading, onClose, actions, children }: { title: ReactNode; heading?: ReactNode; onClose: () => void; actions?: ReactNode; children?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const { onKeyDown, onCancel } = useModalDialog(ref, true, onClose, (dialog) => firstField(dialog) ?? focusables(dialog.querySelector(".drawer-actions") ?? dialog)[0] ?? null);
  return <dialog ref={ref} className="drawer crop-drawer" aria-labelledby={titleId} aria-modal="true" onKeyDown={onKeyDown} onCancel={onCancel}>
    <div className="drawer-form">
      <div className="drawer-head">
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="btn drawer-close" aria-label="Close" onClick={onClose}><span aria-hidden="true">×</span></button>
      </div>
      <div className="drawer-body">
        {heading && <p className="drawer-heading">{heading}</p>}
        {children}
      </div>
      <div className="drawer-actions">
        {actions}
        <Button onClick={onClose}>Done</Button>
      </div>
    </div>
  </dialog>;
}

// ---------------------------------------------------------------------------------------------------- the planting
export type PlantingMode = "move" | "harvest" | "remove" | "edit";
export const PLANTING_MODES: readonly PlantingMode[] = ["move", "harvest", "remove", "edit"];

function Places({ places = [], now }: { places: PlantingDetail["history"]; now: number }) {
  if (!places.length) return <p className="meta">No place recorded.</p>;
  return <ul className="crop-history">{places.map((place, index) => <li key={`${place.cell ?? place.shelf ?? place.zone}-${place.since}-${index}`}>
    <span>{place.cell ?? place.shelf ?? place.zone}</span>
    <span className="meta"> {localDate(place.since)} to {place.until ? localDate(place.until) : `now (${daysText(daysIn(place.since, now))})`}{place.count !== null && place.count !== undefined ? ` · ×${place.count}` : ""}</span>
  </li>)}</ul>;
}

/**
 * A planting's drawer (`?planting=<id>`, API-CROP-03): what it is, where it is and has been, its events and harvests;
 * for an operator Move…, Harvest…, Remove… and Edit…, each a form in this drawer (`&do=`). `selected` is the map's
 * selection: this planting's selected cells (a partial harvest or move from) and the other places (a move to).
 */
export function PlantingDrawer({ id, mode, setMode, onClose, selected }: {
  id: number; mode: PlantingMode | null; setMode: (mode: PlantingMode | null) => void; onClose: () => void; selected?: { own: readonly string[]; other: readonly string[] };
}) {
  const read = useApiRead("API-CROP-03", { planting_id: id }, { intervalMs: 60_000 });
  const now = useClockValue(minute);
  const operator = useRole() === "operator";
  const writesEnabled = useWritesEnabled();
  const planting = read.data;
  const harvests = planting?.harvests ?? [];
  const events = planting?.events ?? [];
  if (!planting) {
    return <ReadDrawer title="Planting" onClose={onClose}>
      {read.isError ? <PanelError thing="this planting" onRetry={() => void read.refetch()} /> : <><div className="skeleton skeleton-md" aria-hidden="true" /><p className="screen-reader">Loading the planting</p></>}
    </ReadDrawer>;
  }
  const back = () => setMode(null);
  if (mode && planting.active && operator) {
    const props = { planting, onClose: back, onDone: back, selected };
    if (mode === "move") return <MoveForm {...props} />;
    if (mode === "harvest") return <HarvestForm {...props} />;
    if (mode === "remove") return <RemoveForm {...props} />;
    if (mode === "edit") return <EditForm {...props} />;
  }
  const actions = operator && planting.active ? <>
    {(["move", "harvest", "remove", "edit"] as const).map((item) => <Button key={item} write disabled={!writesEnabled} aria-haspopup="dialog" onClick={() => setMode(item)}>
      {{ move: "Move…", harvest: "Harvest…", remove: "Remove…", edit: "Edit…" }[item]}
    </Button>)}
  </> : null;
  const state = planting.active ? `In place ${daysText(daysIn(`${planting.planted_date}T12:00:00Z`, now))}` : `${endReasonText(planting.end_reason)} ${planting.ended_at ? localDate(planting.ended_at) : ""}`;
  return <ReadDrawer title="Planting" heading={cropName(planting)} onClose={onClose} actions={actions}>
    <p className="drawer-status">{state} · {countText(planting.count)}{planting.stage ? ` · ${stageText(planting.stage)}` : ""}</p>
    <dl className="kv crop-kv">
      <dt>Where</dt><dd>{planting.where ?? "No place now"}</dd>
      <dt>Planted</dt><dd>{planting.planted_date}</dd>
      {planting.recorded_by && <><dt>Recorded by</dt><dd>{planting.recorded_by.name} ({planting.recorded_by.source})</dd></>}
      {planting.notes && <><dt>Notes</dt><dd>{planting.notes}</dd></>}
    </dl>
    <h3 className="crop-drawer-h">Places over time</h3>
    <Places places={planting.history} now={now} />
    {harvests.length > 0 && <>
      <h3 className="crop-drawer-h">Harvests</h3>
      <ul className="crop-history">{harvests.map((harvest) => <li key={harvest.id}>
        {harvest.ts ? shortDateTime(harvest.ts) : "Undated"}: {[harvest.unit_count !== null && harvest.unit_count !== undefined ? `${harvest.unit_count} plants` : null, harvest.weight_kg !== null && harvest.weight_kg !== undefined ? `${harvest.weight_kg} kg` : null, harvest.notes].filter(Boolean).join(" · ") || "recorded"}
      </li>)}</ul>
    </>}
    {events.length > 0 && <>
      <h3 className="crop-drawer-h">Events</h3>
      <ul className="crop-history">{events.map((event) => <li key={event.id}>
        {event.ts ? shortDateTime(event.ts) : "Undated"}: {eventText(event.event_type)}{event.notes ? ` · ${event.notes}` : ""}{event.operator ? <span className="meta"> · {event.operator}</span> : null}
      </li>)}</ul>
    </>}
  </ReadDrawer>;
}

type FormProps = { planting: PlantingDetail; onClose: () => void; onDone: () => void; selected?: { own: readonly string[]; other: readonly string[] } };

/** Move… (API-CROP-08): from (blank is everywhere it is now) and to, both place texts, previewed; Replace; the date. */
function MoveForm({ planting, onClose, onDone, selected }: FormProps) {
  const now = useClockValue(minute);
  const toast = useToast();
  const write = useCropWrite("API-CROP-08");
  const [from, setFrom] = useState(selected?.own.length ? compressLabels(selected.own).join(", ") : "");
  const [to, setTo] = useState(selected?.other.length ? selected.other.join(", ") : "");
  const [day, setDay] = useState(today(now));
  const [replace, setReplace] = useState(false);
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState<string | null>(null);
  const save = async () => {
    setTyped(to.trim() ? null : "Name where it goes");
    if (!to.trim()) return;
    const outcome = await write.send({ planting_id: planting.id }, {
      to_positions: [to.trim()], ...(from.trim() ? { from_positions: [from.trim()] } : {}), ...(dayOrNow(day, now) ? { moved_at: day } : {}), ...(replace ? { replace: true } : {}),
      ...(reason.trim() ? { reason: reason.trim() } : {}),
    });
    if (!outcome.ok) return;
    toast.show({ message: `Moved ${cropName(planting)} to ${outcome.data.where ?? to.trim()}` });
    onDone();
  };
  return <EditDrawer open onClose={onClose} title="Move planting" heading={cropName(planting)} status={<p className="meta">Now at {planting.where ?? "no place"}</p>}
    onSave={() => void save()} saving={write.sending} saveLabel="Save move" error={formError(write.error, ["to_positions", "from_positions", "moved_at", "reason"])}>
    <TextField label="From (optional)" value={from} onChange={setFrom} maxLength={500} hint="Blank moves it from everywhere it is now" error={errorFor(write.error, "from_positions")} />
    <TextField label="To" value={to} onChange={setTo} maxLength={500} required hint="A cell, a range or a shelf: hydro 41-44, EAST-HYDRO-1..10, west top, south floor"
      error={typed ?? errorFor(write.error, "to_positions")} />
    <PlacesPreview q={to} at={dayOrNow(day, now)} planting={planting.id} label="To" />
    <DateField label="Moved" value={day} onChange={setDay} max={today(now)} error={errorFor(write.error, "moved_at")} />
    <Switch label="Replace what is planted there" checked={replace} onChange={setReplace} />
    <Reason value={reason} onChange={setReason} error={errorFor(write.error, "reason")} />
  </EditDrawer>;
}

/** Harvest… (API-CROP-09): partial by default; cells named are cleared; Final ends the planting as harvested. */
function HarvestForm({ planting, onClose, onDone, selected }: FormProps) {
  const now = useClockValue(minute);
  const toast = useToast();
  const write = useCropWrite("API-CROP-09");
  const [cells, setCells] = useState(selected?.own.length ? compressLabels(selected.own).join(", ") : "");
  const [final, setFinal] = useState(false);
  const [count, setCount] = useState("");
  const [weight, setWeight] = useState("");
  const [day, setDay] = useState(today(now));
  const [notes, setNotes] = useState("");
  const [typed, setTyped] = useState<{ field: string; message: string } | null>(null);
  const save = async () => {
    const units = wholeNumber(count);
    const kg = weight.trim() === "" ? null : Number(weight);
    const problem = units !== null && Number.isNaN(units) ? { field: "count", message: "A whole number" } : kg !== null && (!Number.isFinite(kg) || kg < 0) ? { field: "weight_kg", message: "A number of kilograms" } : null;
    setTyped(problem);
    if (problem) return;
    const outcome = await write.send({ planting_id: planting.id }, {
      final, ...(cells.trim() && !final ? { positions: [cells.trim()] } : {}), ...(units !== null ? { count: units } : {}), ...(kg !== null ? { weight_kg: kg } : {}),
      ...(dayOrNow(day, now) ? { harvested_at: day } : {}), ...(notes.trim() ? { notes: notes.trim() } : {}),
    });
    if (!outcome.ok) return;
    toast.show({ message: outcome.data.active ? `Harvest recorded for ${cropName(planting)}` : `Harvested ${cropName(planting)}; the planting has ended` });
    onDone();
  };
  const error = (field: string) => (typed?.field === field ? typed.message : errorFor(write.error, field));
  return <EditDrawer open onClose={onClose} title="Harvest" heading={cropName(planting)} status={<p className="meta">At {planting.where ?? "no place"}</p>}
    onSave={() => void save()} saving={write.sending} saveLabel="Save harvest" error={formError(write.error, ["positions", "count", "weight_kg", "harvested_at", "notes"])}>
    <Switch label="Final harvest: end the planting" checked={final} onChange={setFinal} />
    {!final && <TextField label="Cells cleared (optional)" value={cells} onChange={setCells} maxLength={500} hint="Blank keeps every cell planted" error={error("positions")} />}
    <NumberField label="Plants harvested (optional)" value={count} onChange={setCount} min={0} step={1} error={error("count")} />
    <NumberField label="Weight (optional)" value={weight} onChange={setWeight} min={0} unit="kg" error={error("weight_kg")} />
    <DateField label="Harvested" value={day} onChange={setDay} max={today(now)} error={error("harvested_at")} />
    <Reason label="Notes (optional)" value={notes} onChange={setNotes} maxLength={2000} error={error("notes")} />
  </EditDrawer>;
}

const END_REASONS = ["removed", "failed", "reconciled"] as const;
/** Remove… (API-CROP-10): ends the planting without a harvest; every place closes. Undo is planting it again. */
function RemoveForm({ planting, onClose, onDone }: FormProps) {
  const now = useClockValue(minute);
  const toast = useToast();
  const write = useCropWrite("API-CROP-10");
  const [reason, setReason] = useState(0);
  const [note, setNote] = useState("");
  const [day, setDay] = useState(today(now));
  const save = async () => {
    const outcome = await write.send({ planting_id: planting.id }, { end_reason: END_REASONS[reason], ...(note.trim() ? { note: note.trim() } : {}), ...(dayOrNow(day, now) ? { at: day } : {}) });
    if (!outcome.ok) return;
    toast.show({ message: `Ended ${cropName(planting)} (${endReasonText(END_REASONS[reason]).toLowerCase()})` });
    onDone();
  };
  return <EditDrawer open onClose={onClose} title="Remove planting" heading={cropName(planting)} status={<p className="meta">Every place it holds ({planting.where ?? "none"}) is cleared. To undo, plant it again.</p>}
    onSave={() => void save()} saving={write.sending} saveLabel="Remove" error={formError(write.error, ["end_reason", "note", "at"])}>
    <Select label="Why" value={reason} options={END_REASONS.map((item, index) => ({ value: index, label: endReasonText(item) }))} onChange={setReason} />
    <DateField label="Ended" value={day} onChange={setDay} max={today(now)} error={errorFor(write.error, "at")} />
    <Reason label="Note (optional)" value={note} onChange={setNote} maxLength={2000} error={errorFor(write.error, "note")} />
  </EditDrawer>;
}

/** Edit… (API-CROP-11): variety, plants (blank is unknown), stage and notes; only what changed is sent. */
function EditForm({ planting, onClose, onDone }: FormProps) {
  const toast = useToast();
  const write = useCropWrite("API-CROP-11");
  const stages: readonly string[] = STAGES;
  const [variety, setVariety] = useState(planting.variety ?? "");
  const [count, setCount] = useState(planting.count === null || planting.count === undefined ? "" : String(planting.count));
  const [stage, setStage] = useState(planting.stage && stages.includes(planting.stage) ? stages.indexOf(planting.stage) : -1);
  const [notes, setNotes] = useState(planting.notes ?? "");
  const [typed, setTyped] = useState<string | null>(null);
  const save = async () => {
    const plants = wholeNumber(count);
    setTyped(plants !== null && Number.isNaN(plants) ? "A whole number, or blank for unknown" : null);
    if (plants !== null && Number.isNaN(plants)) return;
    const body: Record<string, unknown> = {};
    if (variety.trim() !== (planting.variety ?? "")) body.variety = variety.trim() || null;
    if (plants !== (planting.count ?? null)) body.count = plants;
    if (stage >= 0 && stages[stage] !== planting.stage) body.stage = stages[stage];
    if (notes.trim() !== (planting.notes ?? "")) body.notes = notes.trim() || null;
    if (!Object.keys(body).length) { onDone(); return; }
    const outcome = await write.send({ planting_id: planting.id }, body);
    if (!outcome.ok) return;
    toast.show({ message: `Saved ${cropName(outcome.data)}` });
    onDone();
  };
  return <EditDrawer open onClose={onClose} title="Edit planting" heading={planting.crop} onSave={() => void save()} saving={write.sending}
    error={formError(write.error, ["variety", "count", "stage", "notes"])}>
    <TextField label="Variety" value={variety} onChange={setVariety} error={errorFor(write.error, "variety")} />
    <NumberField label="Plants" value={count} onChange={setCount} min={0} step={1} hint="Blank is unknown" error={typed ?? errorFor(write.error, "count")} />
    <Select label="Stage" value={stage < 0 ? null : stage} options={stages.map((item, index) => ({ value: index, label: stageText(item) }))} onChange={setStage} />
    <Reason label="Notes" value={notes} onChange={setNotes} maxLength={2000} error={errorFor(write.error, "notes")} />
  </EditDrawer>;
}

// ---------------------------------------------------------------------------------------------------- a cell
/**
 * A cell's drawer (`?cell=<label>`, API-CROP-04): who has held it, current first, and the irrigation lines that reach
 * its shelf (information only; the hydro rack is hand-dosed and has none).
 */
export function CellDrawer({ label, locations, onClose }: { label: string; locations: Locations | null; onClose: () => void }) {
  const read = useApiRead("API-CROP-04", { label }, { intervalMs: 60_000 });
  const now = useClockValue(minute);
  const href = usePlantingHref();
  const shelf = locations?.zones.flatMap((zone) => zone.shelves ?? []).find((item) => (item.cells ?? []).some((cell) => cell.label === label)) ?? null;
  const lines = shelf?.water_lines ?? [];
  const history = read.data;
  return <ReadDrawer title="Cell" heading={label} onClose={onClose}>
    {shelf && <p className="drawer-status">{shelf.label_short ?? shelf.name}{lines.length ? ` · watered by ${[...new Set(lines.map((line) => line.note ?? line.name))].join(", ")}` : shelf.kind === "nft" ? " · hand-dosed, no irrigation line" : ""}</p>}
    <h3 className="crop-drawer-h">Who has held it</h3>
    {history ? (history.items.length ? <ul className="crop-history">{history.items.map((item) => <li key={`${item.planting_id}-${item.since}`}>
      <Link to={href(item.planting_id)} preventScrollReset>{cropName(item)}</Link>
      <span className="meta"> {localDate(item.since)} to {item.until ? localDate(item.until) : `now (${daysText(daysIn(item.since, now))})`}</span>
    </li>)}</ul> : <p className="meta">Nothing has been planted here.</p>)
      : read.isError ? <PanelError thing="this cell's history" onRetry={() => void read.refetch()} /> : <div className="skeleton skeleton-sm" aria-hidden="true" />}
  </ReadDrawer>;
}
