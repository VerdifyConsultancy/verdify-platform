// SPDX-License-Identifier: MIT
import { useState } from "react";
import { formatNumber } from "../../lib/format";
import type { SoilTarget } from "../../lib/operator-api";
import { useApiRead, READ_INTERVAL_MS } from "../../lib/queries";
import { OperatorOnly } from "../../lib/session";
import { parseDecimal } from "../../lib/setting-values";
import { errorFor, formError, useSoilTargetWrite } from "../../lib/writes";
import { Button } from "../button";
import { LocalTime } from "../common";
import { NumberField, Reason } from "../forms";
import { EmptyState, Panel } from "../panel";
import { actorName } from "../setting-row";

/** The four thresholds in their stored order, with the words the panel and its form use. */
export const SOIL_FIELDS = [
  { key: "wilt_pct", label: "Wilt", hint: "Soil dryout pages when the probe stays below this for over 2 h in the day" },
  { key: "min_pct", label: "Min" },
  { key: "max_pct", label: "Max" },
  { key: "saturation_pct", label: "Saturation" },
] as const;
type SoilKey = (typeof SOIL_FIELDS)[number]["key"];
type Draft = Record<SoilKey, string> & { notes: string; reason: string };

/** "South 1" from `south_1`: the zone as the probe names it, never a crop. */
export const zoneLabel = (zone: string): string => zone.split("_").map((part, index) => (index === 0 ? part.charAt(0).toUpperCase() + part.slice(1) : part)).join(" ");
const pct = (value: number) => `${formatNumber(value)}%`;
const draftOf = (target: SoilTarget): Draft => ({
  wilt_pct: String(target.wilt_pct), min_pct: String(target.min_pct), max_pct: String(target.max_pct), saturation_pct: String(target.saturation_pct),
  notes: target.notes ?? "", reason: "",
});

/**
 * Soil-moisture targets (#1345, API-SOIL-01/02): one row per probe zone, plant-agnostic. Every signed-in user reads
 * them; an operator edits one zone at a time. The server keeps 0 ≤ wilt < min < max < saturation ≤ 100.
 */
export function SoilTargetsPanel() {
  const read = useApiRead("API-SOIL-01", {}, { intervalMs: READ_INTERVAL_MS.settings });
  const targets = read.data?.targets ?? null;
  const [editing, setEditing] = useState<string | null>(null);
  const state = read.isError && !targets ? "error" : !targets ? "loading" : targets.length ? "ready" : "empty";
  return <Panel id="soil-targets" title="Soil targets" thing="the soil targets" state={state} onRetry={() => void read.refetch()}
    empty={<EmptyState message="No soil probes are mapped." />}>
    <p className="meta">Volumetric moisture per probe zone. Wilt is the line the soil dryout page watches.</p>
    <table className="ctl-soil">
      <thead><tr><th scope="col">Zone</th>{SOIL_FIELDS.map((field) => <th key={field.key} scope="col">{field.label}</th>)}<th scope="col">Changed</th><th scope="col"><span className="screen-reader">Edit</span></th></tr></thead>
      <tbody>
        {(targets ?? []).map((target) => <SoilRow key={target.zone} target={target} editing={editing === target.zone}
          onEdit={() => setEditing(target.zone)} onDone={() => setEditing(null)} />)}
      </tbody>
    </table>
  </Panel>;
}

function SoilRow({ target, editing, onEdit, onDone }: { target: SoilTarget; editing: boolean; onEdit: () => void; onDone: () => void }) {
  return <>
    <tr>
      <th scope="row">{zoneLabel(target.zone)}{target.notes && <span className="meta ctl-soil-note"> {target.notes}</span>}</th>
      {SOIL_FIELDS.map((field) => <td key={field.key} className="num">{pct(target[field.key])}</td>)}
      <td className="meta">{target.updated_at ? <><LocalTime at={target.updated_at} />{target.updated_by && <> by {actorName(target.updated_by)}</>}</> : "Not changed"}</td>
      <td><OperatorOnly>{!editing && <Button onClick={onEdit} write aria-label={`Edit ${zoneLabel(target.zone)}`}>Edit</Button>}</OperatorOnly></td>
    </tr>
    {editing && <tr className="ctl-soil-edit"><td colSpan={SOIL_FIELDS.length + 3}><SoilForm target={target} onDone={onDone} /></td></tr>}
  </>;
}

function SoilForm({ target, onDone }: { target: SoilTarget; onDone: () => void }) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(target));
  const write = useSoilTargetWrite();
  const set = (key: keyof Draft) => (text: string) => { write.clearError(); setDraft((current) => ({ ...current, [key]: text })); };
  const save = async () => {
    const body: Record<string, number | string> = {};
    for (const field of SOIL_FIELDS) {
      const value = parseDecimal(draft[field.key]);
      if (value !== null && value !== target[field.key]) body[field.key] = value;
    }
    if (draft.notes !== (target.notes ?? "")) body.notes = draft.notes;
    if (!Object.keys(body).length) { onDone(); return; }
    if (draft.reason.trim()) body.reason = draft.reason.trim();
    const outcome = await write.send(target.zone, body);
    if (outcome.ok) onDone();
  };
  return <form className="ctl-soil-form" aria-label={`Edit ${zoneLabel(target.zone)} soil target`} onSubmit={(event) => { event.preventDefault(); void save(); }}>
    <p className="meta">Keep wilt below min, min below max and max below saturation, all from 0 to 100%.</p>
    <div className="ctl-soil-fields">
      {SOIL_FIELDS.map((field) => <NumberField key={field.key} label={field.label} value={draft[field.key]} onChange={set(field.key)} min={0} max={100} step={1}
        unit="%" hint={"hint" in field ? field.hint : undefined} error={errorFor(write.error, field.key)} disabled={write.sending} />)}
    </div>
    <Reason label="Note (optional)" value={draft.notes} onChange={set("notes")} error={errorFor(write.error, "notes")} disabled={write.sending} />
    <Reason value={draft.reason} onChange={set("reason")} error={errorFor(write.error, "reason")} disabled={write.sending} />
    {formError(write.error, [...SOIL_FIELDS.map((field) => field.key), "notes", "reason"]) && <p className="fault" role="alert">{formError(write.error, [...SOIL_FIELDS.map((field) => field.key), "notes", "reason"])}</p>}
    <div className="ctl-soil-actions">
      <Button type="submit" variant="primary" busy={write.sending} write>Save</Button>
      <Button onClick={onDone} disabled={write.sending}>Cancel</Button>
    </div>
  </form>;
}
