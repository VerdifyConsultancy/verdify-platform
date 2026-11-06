// SPDX-License-Identifier: MIT
import { useCallback, useMemo, useState } from "react";
import { Outlet, useLocation, useOutletContext, useSearchParams } from "react-router";
import { CellDrawer, PLANTING_MODES, PlantingDrawer, type PlantingMode } from "../components/crops/drawers";
import { PageHeader } from "../components/layout/page-header";
import { hydroTally, selectedPlantings, selectionPositions, type PlaceKey } from "../lib/crops";
import type { Locations } from "../lib/operator-api";
import { useApiRead } from "../lib/queries";
import { viewFor, viewMeta } from "../lib/sections";
import type { PolledQueryResult } from "../lib/use-current";

export const meta = () => viewMeta("crops-map");

/** API-CROP-01 once for both views (60 s; a write refetches it at once), and the map's selection, kept under the drawers. */
export type CropsOutlet = {
  readonly locations: PolledQueryResult<Locations>;
  readonly selected: ReadonlySet<PlaceKey>;
  readonly setSelected: (update: (current: ReadonlySet<PlaceKey>) => ReadonlySet<PlaceKey>) => void;
  /** Opens a planting's drawer, in one of its forms when `mode` is given. */
  readonly openPlanting: (id: number, mode?: PlantingMode) => void;
  readonly openCell: (label: string) => void;
};
export const useCropsOutlet = (): CropsOutlet => useOutletContext<CropsOutlet>();

const LOCATIONS_POLL_MS = 60_000;

/** The status line: "12 of 60 hydro cells planted · 3 plantings", from the map's read; null while it loads. */
function status(locations: Locations | undefined): string | null {
  if (!locations) return null;
  const tally = hydroTally(locations);
  const plantings = `${locations.active_plantings} planting${locations.active_plantings === 1 ? "" : "s"}`;
  return tally ? `${tally.planted} of ${tally.total} hydro cells planted · ${plantings}` : plantings;
}

/**
 * Crops (#1330, the sixth section): its page head with the tally, the Map and History tabs, and the drawers both views
 * open from the URL: a planting (`?planting=<id>`, with `&do=move|harvest|remove|edit` for its forms) and a cell
 * (`?cell=<label>`). Plant-agnostic: the page records crops and implies no control target. No greenhouse data renders
 * in SSR.
 */
export default function Crops() {
  const { pathname } = useLocation();
  const view = viewFor(pathname)?.id === "crops-history" ? "crops-history" : "crops-map";
  const locations = useApiRead("API-CROP-01", {}, { intervalMs: LOCATIONS_POLL_MS });
  const [selected, setSelectedState] = useState<ReadonlySet<PlaceKey>>(() => new Set());
  const setSelected = useCallback((update: (current: ReadonlySet<PlaceKey>) => ReadonlySet<PlaceKey>) => setSelectedState(update), []);
  const [params, setParams] = useSearchParams();
  const plantingId = Number(params.get("planting"));
  const mode = PLANTING_MODES.find((item) => item === params.get("do")) ?? null;
  const cell = params.get("cell");
  const change = useCallback((edit: (next: URLSearchParams) => void, replace = false) => setParams((current) => {
    const next = new URLSearchParams(current);
    edit(next);
    return next;
  }, { preventScrollReset: true, replace }), [setParams]);
  const openPlanting = useCallback((id: number, next?: PlantingMode) => change((query) => {
    query.set("planting", String(id));
    if (next) query.set("do", next); else query.delete("do");
    query.delete("cell");
  }), [change]);
  const openCell = useCallback((label: string) => change((query) => { query.set("cell", label); query.delete("planting"); query.delete("do"); }), [change]);
  const closeDrawer = useCallback(() => change((query) => { query.delete("planting"); query.delete("do"); query.delete("cell"); }, true), [change]);
  const setMode = useCallback((next: PlantingMode | null) => change((query) => { if (next) query.set("do", next); else query.delete("do"); }), [change]);
  // A planting's forms start from the map's selection: its own selected cells (move or harvest from) and the rest (move to).
  const forPlanting = useMemo(() => {
    if (!locations.data || !Number.isInteger(plantingId) || plantingId < 1) return undefined;
    const own = selectedPlantings(locations.data, selected).get(plantingId)?.cells ?? [];
    const other = [...selected].filter((key) => !(key.startsWith("cell:") && own.includes(key.slice(5))));
    return { own, other: selectionPositions(other) };
  }, [locations.data, selected, plantingId]);
  const context: CropsOutlet = { locations, selected, setSelected, openPlanting, openCell };
  const line = status(locations.data);
  return <div className="crops-page">
    <PageHeader view={view} status={line} />
    <Outlet context={context} />
    {Number.isInteger(plantingId) && plantingId > 0 && <PlantingDrawer key={plantingId} id={plantingId} mode={mode} setMode={setMode} onClose={closeDrawer} selected={forPlanting} />}
    {cell && !params.get("planting") && <CellDrawer key={cell} label={cell} locations={locations.data ?? null} onClose={closeDrawer} />}
  </div>;
}
