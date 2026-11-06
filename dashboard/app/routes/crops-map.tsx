// SPDX-License-Identifier: MIT
import { useCallback, useId, useMemo, useState, type FormEvent } from "react";
import { Button } from "../components/button";
import { PlantDrawer, type PlantTemplate } from "../components/crops/drawers";
import { CropLegend, MapCardView, type MapSelection } from "../components/crops/map";
import { useClockValue } from "../components/freshness";
import { PanelError } from "../components/panel";
import {
  allCells, cellKey, cellRange, cropTones, legend, mapCards, onlyCell, selectedPlantings, selectionPositions, shelfKey, zoneKey, type PlaceKey,
} from "../lib/crops";
import { ApiError, apiGet } from "../lib/operator-api";
import { viewMeta } from "../lib/sections";
import { useRole } from "../lib/session";
import { problemMessage } from "../lib/writes";
import { useCropsOutlet } from "./crops";

export const meta = () => viewMeta("crops-map");

const minute = (now: number) => Math.floor(now / 60_000) * 60_000;

/**
 * Crops → Map (`/crops`, #1330): the selection bar, the legend and one card per wall or system, drawn from API-CROP-01's
 * layout. Select cells (tap; Shift for a range on one shelf; drag across), a rail, a row or the whole rack (the chips),
 * a shelf or zone as a whole (planters with no cell), or type places ("hydro 1-30", "west top"). Then an operator plants
 * them (Plant…), or acts on the one planting they hold (Move…, Harvest…, Remove…, Edit…). Re-plant mode keeps the crop
 * and date, so each next selection is Plant… and Save. A viewer sees the same map, the selection and Details.
 */
export default function CropsMap() {
  const { locations, selected, setSelected, openPlanting, openCell } = useCropsOutlet();
  const now = useClockValue(minute);
  const operator = useRole() === "operator";
  const [anchor, setAnchor] = useState<string | null>(null);
  const [planting, setPlanting] = useState(false);
  const [template, setTemplate] = useState<PlantTemplate | null>(null);
  const data = locations.data;
  const cards = useMemo(() => (data ? mapCards(data) : []), [data]);
  const tones = useMemo(() => (data ? cropTones(data) : new Map<string, number>()), [data]);
  const toggle = useCallback((keys: readonly PlaceKey[], on?: boolean) => setSelected((current) => {
    const next = new Set(current);
    const add = on ?? !keys.every((key) => current.has(key));
    for (const key of keys) if (add) next.add(key); else next.delete(key);
    return next;
  }), [setSelected]);
  const selection: MapSelection = {
    selected,
    onCell: (shelf, label, range) => {
      if (range && anchor) toggle(cellRange(shelf, anchor, label).map(cellKey), true);
      else toggle([cellKey(label)]);
      setAnchor(label);
    },
    onDrag: (label, add) => toggle([cellKey(label)], add),
    onPlace: (key) => toggle([key]),
    onGroup: (labels) => toggle(labels.map(cellKey)),
  };
  if (!data) {
    if (locations.isError) return <PanelError thing="the crop map" onRetry={() => void locations.refetch()} />;
    return <div className="crop-map" aria-busy="true"><div className="skeleton skeleton-lg" aria-hidden="true" /><p className="screen-reader">Loading the crop map</p></div>;
  }
  const positions = selectionPositions(selected);
  const known = new Set(allCells(data).map((cell) => cell.label));
  const cellCount = [...selected].filter((key) => key.startsWith("cell:") && known.has(key.slice(5))).length;
  const held = selectedPlantings(data, selected);
  const one = held.size === 1 ? [...held.values()][0] : null;
  const single = onlyCell(selected);
  return <div className="crop-map">
    <div className="crop-toolbar" role="region" aria-label="Selection">
      <p className="crop-toolbar-what" aria-live="polite">
        {selected.size ? <><strong>{selected.size} selected:</strong> {positions.join(", ")}</> : "Nothing selected. Tap cells, drag across them, or use the rail and shelf buttons."}
      </p>
      <div className="crop-toolbar-actions">
        {operator && <Button write variant="primary" disabled={!selected.size} aria-haspopup="dialog" onClick={() => setPlanting(true)}>
          {template ? `Plant ${template.crop}…` : "Plant…"}
        </Button>}
        {operator && one && <>
          <Button write aria-haspopup="dialog" onClick={() => openPlanting(one.planting_id, "move")}>Move…</Button>
          <Button write aria-haspopup="dialog" onClick={() => openPlanting(one.planting_id, "harvest")}>Harvest…</Button>
          <Button write aria-haspopup="dialog" onClick={() => openPlanting(one.planting_id, "remove")}>Remove…</Button>
          <Button write aria-haspopup="dialog" onClick={() => openPlanting(one.planting_id, "edit")}>Edit…</Button>
        </>}
        {single && <Button aria-haspopup="dialog" onClick={() => openCell(single)}>Cell details</Button>}
        {one && <Button aria-haspopup="dialog" onClick={() => openPlanting(one.planting_id)}>Planting details</Button>}
        {selected.size > 0 && <Button onClick={() => { setSelected(() => new Set()); setAnchor(null); }}>Clear</Button>}
        {template && operator && <Button onClick={() => setTemplate(null)}>Stop re-planting {template.crop}</Button>}
      </div>
      {held.size > 1 && <p className="meta">The selection holds {held.size} plantings; select one planting's cells to move, harvest or remove it.</p>}
      <PlaceSelect onFound={(keys) => toggle(keys, true)} />
    </div>
    <CropLegend items={legend(data)} />
    <div className="crop-cards">
      {cards.map((card) => <MapCardView key={card.key} card={card} tones={tones} now={now} selection={selection} />)}
    </div>
    {planting && <PlantDrawer positions={positions} cellCount={cellCount} template={template} onClose={() => setPlanting(false)}
      onSaved={(kept) => { setTemplate(kept); setPlanting(false); setSelected(() => new Set()); setAnchor(null); }} />}
  </div>;
}

/**
 * The place box: a place text ("hydro 1-30", "rail A top", "west top", "south floor") resolved by API-CROP-06 and added
 * to the selection, so a whole re-plant is typed rather than tapped. An unreadable or ambiguous text says why.
 */
function PlaceSelect({ onFound }: { onFound: (keys: PlaceKey[]) => void }) {
  const id = useId();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      const found = await apiGet("API-CROP-06", { q: text.trim() });
      onFound(found.places.map((place) => (place.cell ? cellKey(place.cell) : place.shelf ? shelfKey(place.shelf) : zoneKey(place.zone))));
      setError(null);
      setText("");
    } catch (caught) {
      setError(caught instanceof ApiError ? problemMessage(caught.problem) : "Couldn't read those places");
    } finally {
      setBusy(false);
    }
  };
  return <form className="crop-place-form" onSubmit={(event) => void submit(event)} noValidate>
    <label htmlFor={id}>Select by place</label>
    <div className="field-row">
      <input id={id} type="text" value={text} maxLength={500} autoComplete="off" placeholder="hydro 1-30, rail A top, west top" aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined} onChange={(event) => setText(event.target.value)} />
      <Button type="submit" busy={busy}>Select</Button>
    </div>
    {error && <p id={`${id}-error`} className="field-error" role="alert">{error}</p>}
  </form>;
}
