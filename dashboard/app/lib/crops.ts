// SPDX-License-Identifier: MIT
/**
 * The Crops section's model (#1330): the map's cards and grids drawn from API-CROP-01's layout data (shelf `wall`,
 * `display_order` and `label_short`; cell `layout_group`, `layout_row` and `layout_col`), never from a hard-coded
 * greenhouse; the selection and the place texts a write sends; a crop's colour on the map; and days in place. The page
 * records crops only: nothing here reads or sets a control target.
 */
import type { CropCell, CropShelf, Locations, Occupant } from "./operator-api";

export const DAY_MS = 86_400_000;

// ---------------------------------------------------------------------------------------------------- the map
/** One group of cells drawn as a grid: a hydro rail, a south bay pair, or a whole shelf row. */
export type CellGrid = {
  /** "Rail A", "Southeast", or null when the shelf has one group. */
  readonly label: string | null;
  /** Columns in the grid (`crop-cols-N`). */
  readonly columns: number;
  /** Row by row, left to right as seen from the walkway; null is a gap in the layout. */
  readonly rows: readonly (readonly (CropCell | null)[])[];
};
export type ShelfStrip = { readonly shelf: CropShelf; readonly grids: readonly CellGrid[] };
/** One card of the map: a wall's shelves, the hydro rack, or the center. */
export type MapCard = {
  readonly key: string;
  readonly title: string;
  /** The zone slugs the card covers, for a zone-level planting and for "Whole zone". */
  readonly zones: readonly string[];
  readonly strips: readonly ShelfStrip[];
  /** Plantings at zone level in this card's zones (no shelf named). */
  readonly zonePlantings: readonly Occupant[];
};

const WALL_TITLES: Readonly<Record<string, string>> = { north: "North wall", east: "East wall", south: "South wall", west: "West wall", center: "Center" };
const GROUP_TITLES: Readonly<Record<string, string>> = { SE: "Southeast bays", SW: "Southwest bays" };
/** "Rail A" for a hydro rail, "Southeast bays" for SE, else the group as stored. */
export function groupTitle(shelf: Pick<CropShelf, "kind">, group: string): string {
  if (shelf.kind === "nft" || shelf.kind === "hydro") return `Rail ${group}`;
  return GROUP_TITLES[group] ?? group;
}
/** The shelf's short name ("West top"), else its name. */
export const shelfTitle = (shelf: Pick<CropShelf, "label_short" | "name">): string => shelf.label_short ?? shelf.name;
const isRack = (shelf: Pick<CropShelf, "kind">) => shelf.kind === "nft" || shelf.kind === "hydro";

const cellSort = (a: CropCell, b: CropCell) => (a.layout_row ?? 0) - (b.layout_row ?? 0) || (a.layout_col ?? 0) - (b.layout_col ?? 0) || (a.number ?? 0) - (b.number ?? 0);
/** A cell's column, or its place in the shelf's order when the layout has none. */
function gridOf(shelf: CropShelf, label: string | null, cells: readonly CropCell[]): CellGrid {
  const laid = cells.every((cell) => cell.layout_row !== null && cell.layout_col !== null);
  if (!laid) {
    const sorted = [...cells].sort(cellSort);
    return { label, columns: Math.min(Math.max(sorted.length, 1), 10), rows: chunk(sorted, 10) };
  }
  const cols = cells.map((cell) => cell.layout_col!);
  const rowsAt = [...new Set(cells.map((cell) => cell.layout_row!))].sort((a, b) => a - b);
  const first = Math.min(...cols);
  const columns = Math.max(...cols) - first + 1;
  const rows = rowsAt.map((row) => Array.from({ length: columns }, (_, index) => cells.find((cell) => cell.layout_row === row && cell.layout_col === first + index) ?? null));
  return { label, columns, rows };
}
function chunk<T>(items: readonly T[], size: number): T[][] {
  const out: T[][] = [];
  for (let index = 0; index < items.length; index += size) out.push(items.slice(index, index + size));
  return out;
}
/** A shelf's cells as grids: one per layout group (rails A to C, the SE and SW bays), else one. */
export function shelfGrids(shelf: CropShelf): CellGrid[] {
  const cells = shelf.cells ?? [];
  if (!cells.length) return [];
  const groups = [...new Set(cells.map((cell) => cell.layout_group ?? ""))].sort();
  if (groups.length === 1 && !(groups[0] && isRack(shelf))) return [gridOf(shelf, null, cells)];
  return groups.map((group) => gridOf(shelf, group ? groupTitle(shelf, group) : null, cells.filter((cell) => (cell.layout_group ?? "") === group)));
}

/**
 * The map's cards: the hydro rack first (the system Jason re-plants), then one card per wall in `display_order`
 * (Center, East, South, West today), each holding its shelves in order. A shelf with no wall joins its zone's card.
 * Zones with no shelf (north) draw no card unless a planting is recorded at zone level there.
 */
export function mapCards(locations: Pick<Locations, "zones">): MapCard[] {
  const shelves = locations.zones.flatMap((zone) => (zone.shelves ?? []).map((shelf) => ({ shelf, zone: zone.slug })));
  const order = (shelf: CropShelf) => shelf.display_order ?? Number.MAX_SAFE_INTEGER;
  const cards = new Map<string, { title: string; zones: Set<string>; shelves: CropShelf[]; first: number; rack: boolean }>();
  for (const { shelf, zone } of shelves) {
    const rack = isRack(shelf);
    const wall = shelf.wall ?? zone;
    const key = rack ? `rack-${shelf.slug}` : `wall-${wall}`;
    const card = cards.get(key) ?? { title: rack ? `${shelfTitle(shelf)} rack` : WALL_TITLES[wall] ?? `${wall[0].toUpperCase()}${wall.slice(1)}`, zones: new Set<string>(), shelves: [], first: order(shelf), rack };
    card.zones.add(zone);
    card.shelves.push(shelf);
    card.first = Math.min(card.first, order(shelf));
    cards.set(key, card);
  }
  const zonePlantings = new Map(locations.zones.map((zone) => [zone.slug, zone.plantings ?? []]));
  // A zone-level planting shows on the wall card that holds its zone's shelves, else on a card of its own.
  for (const zone of locations.zones) {
    if (!(zone.plantings ?? []).length || [...cards.values()].some((card) => !card.rack && card.zones.has(zone.slug))) continue;
    cards.set(`zone-${zone.slug}`, { title: WALL_TITLES[zone.slug] ?? zone.name, zones: new Set([zone.slug]), shelves: [], first: Number.MAX_SAFE_INTEGER, rack: false });
  }
  return [...cards.entries()]
    .sort(([, a], [, b]) => Number(b.rack) - Number(a.rack) || a.first - b.first)
    .map(([key, card]) => ({
      key, title: card.title, zones: [...card.zones],
      strips: [...card.shelves].sort((a, b) => order(a) - order(b)).map((shelf) => ({ shelf, grids: shelfGrids(shelf) })),
      zonePlantings: card.rack ? [] : [...card.zones].flatMap((zone) => zonePlantings.get(zone) ?? []),
    }));
}

/** Every cell of the tree, in map order. */
export const allCells = (locations: Pick<Locations, "zones">): CropCell[] => locations.zones.flatMap((zone) => (zone.shelves ?? []).flatMap((shelf) => shelf.cells ?? []));
/** The hydro tally for the status line: "12 of 60 hydro cells planted". Null with no rack. */
export function hydroTally(locations: Pick<Locations, "zones">): { planted: number; total: number } | null {
  const cells = locations.zones.flatMap((zone) => (zone.shelves ?? []).filter(isRack).flatMap((shelf) => shelf.cells ?? []));
  return cells.length ? { planted: cells.filter((cell) => cell.occupant).length, total: cells.length } : null;
}

// ---------------------------------------------------------------------------------------------------- crop colours
/** Distinct crop tones on the map; a ninth crop reuses the first tone, and the legend still names it. */
export const CROP_TONES = 8;
/** Each crop's tone (`crop-tone-N`), in the order crops first appear on the map, so a colour is stable while it stays. */
export function cropTones(locations: Pick<Locations, "zones">): Map<string, number> {
  const tones = new Map<string, number>();
  const see = (occupant: Occupant | null | undefined) => {
    if (occupant && !tones.has(occupant.crop)) tones.set(occupant.crop, tones.size % CROP_TONES);
  };
  for (const card of mapCards(locations)) {
    card.zonePlantings.forEach(see);
    for (const { shelf } of card.strips) {
      (shelf.plantings ?? []).forEach(see);
      for (const cell of [...(shelf.cells ?? [])].sort(cellSort)) see(cell.occupant);
    }
  }
  return tones;
}
/** The legend: each crop with its tone and how many cells and places it holds. */
export function legend(locations: Pick<Locations, "zones">): { crop: string; tone: number; cells: number; places: number }[] {
  const tones = cropTones(locations);
  const counts = new Map<string, { cells: number; places: number }>();
  const add = (crop: string, cell: boolean) => {
    const entry = counts.get(crop) ?? { cells: 0, places: 0 };
    if (cell) entry.cells += 1; else entry.places += 1;
    counts.set(crop, entry);
  };
  for (const zone of locations.zones) {
    for (const occupant of zone.plantings ?? []) add(occupant.crop, false);
    for (const shelf of zone.shelves ?? []) {
      for (const occupant of shelf.plantings ?? []) add(occupant.crop, false);
      for (const cell of shelf.cells ?? []) if (cell.occupant) add(cell.occupant.crop, true);
    }
  }
  return [...tones.entries()].map(([crop, tone]) => ({ crop, tone, ...(counts.get(crop) ?? { cells: 0, places: 0 }) }));
}

// ---------------------------------------------------------------------------------------------------- words
/** Whole days since `since` on the clock `now` (0 on the day it went in). */
export const daysIn = (since: string, now: number): number => Math.max(0, Math.floor((now - Date.parse(since)) / DAY_MS));
export const daysText = (days: number): string => `${days} d`;
/** "Lettuce · Butterhead", or the crop alone. */
export const cropName = (planting: Pick<Occupant, "crop" | "variety">): string => (planting.variety ? `${planting.crop} · ${planting.variety}` : planting.crop);
/** "×10", "count unknown" when null. */
export const countText = (count: number | null | undefined): string => (count === null || count === undefined ? "count unknown" : `×${count}`);
/** A cell's accessible name: "EAST-HYDRO-12, Lettuce, Butterhead, 12 days", or "EAST-HYDRO-12, empty". */
export function cellName(cell: Pick<CropCell, "label" | "occupant">, now: number): string {
  if (!cell.occupant) return `${cell.label}, empty`;
  const days = daysIn(cell.occupant.since, now);
  return `${cell.label}, ${[cell.occupant.crop, cell.occupant.variety].filter(Boolean).join(", ")}, ${days} day${days === 1 ? "" : "s"}`;
}
/** A cell's short number on the map: "12" for EAST-HYDRO-12, "T3" for WEST-SHELF-T3, "1" for CENTER-HANG-1. */
export function cellShort(label: string): string {
  const match = /([A-Z]?\d+)$/.exec(label);
  return match ? match[1] : label;
}
/** A crop's few-letter mark for a narrow hydro cell: "Let" for Lettuce. */
export const cropMark = (crop: string): string => crop.replace(/[^\p{L}\p{N}]/gu, "").slice(0, 3);

// ---------------------------------------------------------------------------------------------------- selection
/** A selected place: a cell by its label, or a shelf or zone as a whole (a planting with planters on it). */
export type PlaceKey = `cell:${string}` | `shelf:${string}` | `zone:${string}`;
export const cellKey = (label: string): PlaceKey => `cell:${label}`;
export const shelfKey = (slug: string): PlaceKey => `shelf:${slug}`;
export const zoneKey = (slug: string): PlaceKey => `zone:${slug}`;
const NUMBER = /(\d+)$/;
const prefixAndNumber = (label: string): [string, number | null] => {
  const match = NUMBER.exec(label);
  return match ? [label.slice(0, match.index), Number(match[1])] : [label, null];
};
/** Cell labels as the fewest place texts: consecutive numbers on one prefix as `EAST-HYDRO-1..10`, as the server writes them. */
export function compressLabels(labels: Iterable<string>): string[] {
  const keyed = [...new Set(labels)].map(prefixAndNumber).sort(([pa, na], [pb, nb]) => (pa < pb ? -1 : pa > pb ? 1 : (na ?? -1) - (nb ?? -1)));
  const out: string[] = [];
  let run: [string, number][] = [];
  const flush = () => {
    if (run.length) out.push(run.length === 1 ? `${run[0][0]}${run[0][1]}` : `${run[0][0]}${run[0][1]}..${run[run.length - 1][1]}`);
    run = [];
  };
  for (const [prefix, number] of keyed) {
    if (number === null) { flush(); out.push(prefix); continue; }
    const last = run[run.length - 1];
    if (last && last[0] === prefix && last[1] + 1 === number) run.push([prefix, number]);
    else { flush(); run.push([prefix, number]); }
  }
  flush();
  return out;
}
/** The `positions` a write sends for a selection: cell ranges, then shelves, then zones. */
export function selectionPositions(selected: Iterable<PlaceKey>): string[] {
  const keys = [...selected];
  const of = (kind: string) => keys.filter((key) => key.startsWith(`${kind}:`)).map((key) => key.slice(kind.length + 1));
  return [...compressLabels(of("cell")), ...of("shelf"), ...of("zone")];
}
/** The selection's one cell, for Details, or null. */
export function onlyCell(selected: ReadonlySet<PlaceKey>): string | null {
  if (selected.size !== 1) return null;
  const [key] = [...selected];
  return key.startsWith("cell:") ? key.slice(5) : null;
}
/** The selection in words: "EAST-HYDRO-1..10, west_shelf_top". */
export const selectionText = (selected: Iterable<PlaceKey>): string => selectionPositions(selected).join(", ");
/**
 * Shift-click: the cells of one shelf from `anchor` to `target` by number (hydro 5 to 14 is 5…14 across a rail
 * boundary, as the rack is numbered). Cells of different shelves give just the target.
 */
export function cellRange(shelf: Pick<CropShelf, "cells">, anchor: string, target: string): string[] {
  const cells = [...(shelf.cells ?? [])].sort((a, b) => (a.number ?? 0) - (b.number ?? 0) || cellSort(a, b));
  const from = cells.findIndex((cell) => cell.label === anchor);
  const to = cells.findIndex((cell) => cell.label === target);
  if (from < 0 || to < 0) return [target];
  const [a, b] = from <= to ? [from, to] : [to, from];
  return cells.slice(a, b + 1).map((cell) => cell.label);
}
/** The one-tap groups of the hydro rack: each rail, each row and the whole rack. */
export function rackGroups(shelf: CropShelf): { key: string; label: string; cells: string[] }[] {
  const cells = shelf.cells ?? [];
  const rails = [...new Set(cells.map((cell) => cell.layout_group).filter((group): group is string => Boolean(group)))].sort();
  const rows = [...new Set(cells.map((cell) => cell.layout_row).filter((row): row is number => row !== null && row !== undefined))].sort((a, b) => a - b);
  const rowName = (row: number) => (rows.length === 2 ? (row === rows[0] ? "Top row" : "Bottom row") : `Row ${row + 1}`);
  return [
    ...rails.map((rail) => ({ key: `rail-${rail}`, label: `Rail ${rail}`, cells: cells.filter((cell) => cell.layout_group === rail).map((cell) => cell.label) })),
    ...rows.map((row) => ({ key: `row-${row}`, label: rowName(row), cells: cells.filter((cell) => cell.layout_row === row).map((cell) => cell.label) })),
    { key: "all", label: `All ${shelfTitle(shelf).toLowerCase()}`, cells: cells.map((cell) => cell.label) },
  ];
}
/** The plantings a selection touches: each cell's occupant and each selected shelf's or zone's plantings, by id. */
export function selectedPlantings(locations: Pick<Locations, "zones">, selected: ReadonlySet<PlaceKey>): Map<number, Occupant & { cells: string[] }> {
  const found = new Map<number, Occupant & { cells: string[] }>();
  const add = (occupant: Occupant, cell: string | null) => {
    const entry = found.get(occupant.planting_id) ?? { ...occupant, cells: [] };
    if (cell) entry.cells.push(cell);
    found.set(occupant.planting_id, entry);
  };
  for (const zone of locations.zones) {
    if (selected.has(zoneKey(zone.slug))) for (const occupant of zone.plantings ?? []) add(occupant, null);
    for (const shelf of zone.shelves ?? []) {
      if (selected.has(shelfKey(shelf.slug))) for (const occupant of shelf.plantings ?? []) add(occupant, null);
      for (const cell of shelf.cells ?? []) if (cell.occupant && selected.has(cellKey(cell.label))) add(cell.occupant, cell.label);
    }
  }
  return found;
}

// ---------------------------------------------------------------------------------------------------- words
/** End reasons and stages in words. */
export const END_REASON_WORDS: Readonly<Record<string, string>> = { harvested: "Harvested", failed: "Failed", removed: "Removed", reconciled: "Reconciled", moved_out: "Moved out" };
export const endReasonText = (reason: string | null | undefined): string => (reason ? END_REASON_WORDS[reason] ?? reason : "");
export const STAGES = ["seed", "germination", "seedling", "vegetative", "flowering", "fruiting", "harvest"] as const;
export const stageText = (stage: string): string => `${stage[0].toUpperCase()}${stage.slice(1)}`;
const EVENT_WORDS: Readonly<Record<string, string>> = {
  planted: "Planted", moved: "Moved", harvested: "Harvested", note: "Note", reconciled: "Reconciled", stage_change: "Stage changed", removed: "Removed", failed: "Failed",
};
/** A planting event's type in words; an unknown type shows raw. */
export const eventText = (type: string): string => EVENT_WORDS[type] ?? type;
