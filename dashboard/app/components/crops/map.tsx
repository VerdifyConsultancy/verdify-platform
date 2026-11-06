// SPDX-License-Identifier: MIT
import type { MouseEvent, PointerEvent } from "react";
import { Link, useLocation } from "react-router";
import {
  cellKey, cellName, cellShort, cropMark, cropName, countText, daysIn, daysText, rackGroups, shelfKey, shelfTitle, zoneKey, type CellGrid, type MapCard,
  type PlaceKey,
} from "../../lib/crops";
import type { CropCell, CropShelf, Occupant } from "../../lib/operator-api";
import { cls } from "../common";

/** What the map asks its page to do with the selection. */
export type MapSelection = {
  readonly selected: ReadonlySet<PlaceKey>;
  /** A cell tapped: alone, or with Shift as the range from the last cell tapped on its shelf. */
  readonly onCell: (shelf: CropShelf, label: string, range: boolean) => void;
  /** A drag across cells adds each cell it enters (or takes each away, when it started on a selected cell). */
  readonly onDrag: (label: string, add: boolean) => void;
  /** A shelf or zone as a whole, for planters on it (several crops on one shelf). */
  readonly onPlace: (key: PlaceKey) => void;
  /** A group chip: every cell of a rail, a row or the rack. */
  readonly onGroup: (labels: readonly string[]) => void;
};

/** `?planting=<id>` on the current page, keeping the rest of its query. */
export function usePlantingHref() {
  const { pathname, search } = useLocation();
  return (id: number) => {
    const next = new URLSearchParams(search);
    next.set("planting", String(id));
    next.delete("do");
    next.delete("cell");
    return `${pathname}?${next}`;
  };
}

/** A shelf- or zone-level planting: a link to its drawer, with its tone, count and days in place. */
function PlantingChip({ occupant, tone, now }: { occupant: Occupant; tone: number; now: number }) {
  const href = usePlantingHref();
  return <li>
    <Link className={cls("crop-chip", `crop-tone-${tone}`)} to={href(occupant.planting_id)} preventScrollReset>
      <span className="crop-chip-name">{cropName(occupant)}</span>
      <span className="crop-chip-meta">{countText(occupant.count)} · {daysText(daysIn(occupant.since, now))}</span>
    </Link>
  </li>;
}

function Cell({ shelf, cell, tone, now, wide, selection }: { shelf: CropShelf; cell: CropCell; tone: number | null; now: number; wide: boolean; selection: MapSelection }) {
  const pressed = selection.selected.has(cellKey(cell.label));
  const occupant = cell.occupant;
  const onClick = (event: MouseEvent<HTMLButtonElement>) => selection.onCell(shelf, cell.label, event.shiftKey);
  const onPointerEnter = (event: PointerEvent<HTMLButtonElement>) => {
    const drag = dragState.current;
    if (!drag || event.buttons !== 1 || drag.from === cell.label) return;
    // The cell the drag started on takes the drag's action too: a release elsewhere never clicks it.
    if (!drag.moved) selection.onDrag(drag.from, drag.add);
    drag.moved = true;
    selection.onDrag(cell.label, drag.add);
  };
  return <button type="button" className={cls("crop-cell", occupant ? `crop-tone-${tone ?? 0}` : "crop-cell-empty", pressed && "crop-cell-selected")} aria-pressed={pressed}
    aria-label={cellName(cell, now)} title={cellName(cell, now)} data-cell={cell.label}
    onPointerDown={(event) => { if (event.button === 0 && !event.shiftKey) dragState.current = { from: cell.label, add: !pressed, moved: false }; }}
    onPointerEnter={onPointerEnter} onClick={onClick}>
    <span className="crop-cell-no" aria-hidden="true">{cellShort(cell.label)}</span>
    {occupant && <>
      <span className="crop-cell-crop" aria-hidden="true">{wide ? occupant.crop : cropMark(occupant.crop)}</span>
      {wide && occupant.variety && <span className="crop-cell-variety" aria-hidden="true">{occupant.variety}</span>}
      <span className="crop-cell-days" aria-hidden="true">{daysText(daysIn(occupant.since, now))}</span>
    </>}
  </button>;
}
/** The one drag in progress (a pointer held down on a cell), shared by every cell of the map. */
const dragState: { current: { from: string; add: boolean; moved: boolean } | null } = { current: null };
if (typeof window !== "undefined") window.addEventListener("pointerup", () => { dragState.current = null; });

function Grid({ shelf, grid, tones, now, wide, selection }: { shelf: CropShelf; grid: CellGrid; tones: ReadonlyMap<string, number>; now: number; wide: boolean; selection: MapSelection }) {
  return <div className="crop-grid-group">
    {grid.label && <p className="crop-grid-label">{grid.label}</p>}
    <div className={cls("crop-grid", `crop-cols-${Math.min(grid.columns, 10)}`)}>
      {grid.rows.flatMap((row, rowIndex) => row.map((cell, colIndex) => (cell
        ? <Cell key={cell.label} shelf={shelf} cell={cell} tone={cell.occupant ? tones.get(cell.occupant.crop) ?? 0 : null} now={now} wide={wide} selection={selection} />
        : <span key={`gap-${rowIndex}-${colIndex}`} className="crop-cell-gap" aria-hidden="true" />)))}
    </div>
  </div>;
}

function Strip({ shelf, grids, tones, now, selection, rack }: { shelf: CropShelf; grids: readonly CellGrid[]; tones: ReadonlyMap<string, number>; now: number; selection: MapSelection; rack: boolean }) {
  const whole = selection.selected.has(shelfKey(shelf.slug));
  const plantings = shelf.plantings ?? [];
  const water = (shelf.water_lines ?? []).map((line) => line.note ?? line.name);
  return <section className="crop-strip" aria-label={shelfTitle(shelf)}>
    <div className="crop-strip-head">
      <h3>{shelfTitle(shelf)}</h3>
      {water.length > 0 && <p className="meta crop-strip-water">{[...new Set(water)].join(" · ")}</p>}
      {!rack && <button type="button" className="chip crop-place-toggle" aria-pressed={whole} onClick={() => selection.onPlace(shelfKey(shelf.slug))}>
        Whole shelf<span className="screen-reader">: {shelfTitle(shelf)}</span>
      </button>}
    </div>
    {rack && <ul className="chips chips-sm crop-groups" aria-label={`Select on the ${shelfTitle(shelf).toLowerCase()} rack`}>
      {rackGroups(shelf).map((group) => <li key={group.key}><button type="button" className="chip" onClick={() => selection.onGroup(group.cells)}>{group.label}</button></li>)}
    </ul>}
    {plantings.length > 0 && <ul className="crop-chips" aria-label={`Planted on ${shelfTitle(shelf)}`}>
      {plantings.map((occupant) => <PlantingChip key={occupant.planting_id} occupant={occupant} tone={tones.get(occupant.crop) ?? 0} now={now} />)}
    </ul>}
    {grids.length > 0 && <div className={cls("crop-grids", rack && "crop-grids-rack")}>
      {rack
        ? <div className="crop-rack-scroll" role="region" tabIndex={0} aria-label={`${shelfTitle(shelf)} cells, scrolls sideways`}>
          <div className="crop-rack">{grids.map((grid) => <Grid key={grid.label ?? "all"} shelf={shelf} grid={grid} tones={tones} now={now} wide={false} selection={selection} />)}</div>
        </div>
        : grids.map((grid) => <Grid key={grid.label ?? "all"} shelf={shelf} grid={grid} tones={tones} now={now} wide selection={selection} />)}
    </div>}
  </section>;
}

/**
 * One card of the map (#1330): a wall's shelves, the hydro rack or the center, drawn from the layout data. A cell shows
 * its number, its crop (a three-letter mark on the rack, the name and variety on a shelf) and days in place, tinted by
 * crop; an empty cell is blank. Each cell is a toggle for the selection; a shelf's planters (plantings with no cell)
 * are links to their drawer, and "Whole shelf" selects the shelf as a place. Nothing here is a control target.
 */
export function MapCardView({ card, tones, now, selection }: { card: MapCard; tones: ReadonlyMap<string, number>; now: number; selection: MapSelection }) {
  const rack = card.key.startsWith("rack-");
  return <section className="card crop-card" aria-labelledby={`crop-card-${card.key}`}>
    <div className="card-head">
      <div className="card-title"><div className="card-title-row"><h2 id={`crop-card-${card.key}`}>{card.title}</h2></div></div>
      {!rack && card.zones.length === 1 && card.strips.length === 0 && <button type="button" className="chip crop-place-toggle" aria-pressed={selection.selected.has(zoneKey(card.zones[0]))} onClick={() => selection.onPlace(zoneKey(card.zones[0]))}>Whole zone</button>}
    </div>
    {card.zonePlantings.length > 0 && <ul className="crop-chips" aria-label={`Planted in the ${card.title.toLowerCase()} zone`}>
      {card.zonePlantings.map((occupant) => <PlantingChip key={occupant.planting_id} occupant={occupant} tone={tones.get(occupant.crop) ?? 0} now={now} />)}
    </ul>}
    {card.strips.map(({ shelf, grids }) => <Strip key={shelf.slug} shelf={shelf} grids={grids} tones={tones} now={now} selection={selection} rack={rack} />)}
  </section>;
}

/** The legend: each crop on the map with its tint and how much it holds. */
export function CropLegend({ items }: { items: readonly { crop: string; tone: number; cells: number; places: number }[] }) {
  if (!items.length) return <p className="meta crop-legend-empty">Nothing is planted yet.</p>;
  return <ul className="crop-legend" aria-label="Crops on the map">
    {items.map((item) => <li key={item.crop}>
      <span className={cls("crop-swatch", `crop-tone-${item.tone}`)} aria-hidden="true" />
      <span>{item.crop}</span>
      <span className="meta">{[item.cells ? `${item.cells} cell${item.cells === 1 ? "" : "s"}` : null, item.places ? `${item.places} place${item.places === 1 ? "" : "s"}` : null].filter(Boolean).join(", ")}</span>
    </li>)}
  </ul>;
}
