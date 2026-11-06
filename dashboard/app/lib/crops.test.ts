// SPDX-License-Identifier: MIT
// #1330's map model on the fixture layout (migration 293's tree): the cards in order, the hydro rack's rails and rows,
// the selection as place texts, Shift ranges, the plantings a selection touches, and the hydro tally.
import { expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import {
  allCells, cellKey, cellRange, compressLabels, hydroTally, legend, mapCards, onlyCell, rackGroups, selectedPlantings, selectionPositions, shelfKey, zoneKey,
} from "./crops";
import { parseLocations, type CropShelf, type Locations } from "./operator-api";

const NOW = "2026-11-02T17:42:00.000Z";
const layout = (name = "in-band"): Locations => parseLocations(fixture(name, { now: NOW })["API-CROP-01"])!;
const hydro = (locations: Locations): CropShelf => locations.zones.flatMap((zone) => zone.shelves ?? []).find((shelf) => shelf.slug === "east_hydro")!;

test("the cards: the hydro rack first, then each wall, every shelf drawn once", () => {
  const locations = layout();
  const cards = mapCards(locations);
  expect(cards[0].strips.map((strip) => strip.shelf.slug)).toEqual(["east_hydro"]);
  const slugs = cards.flatMap((card) => card.strips.map((strip) => strip.shelf.slug)).sort();
  expect(slugs).toEqual(locations.zones.flatMap((zone) => (zone.shelves ?? []).map((shelf) => shelf.slug)).sort());
  expect(allCells(locations).filter((cell) => cell.label.startsWith("EAST-HYDRO-"))).toHaveLength(60);
});

test("the hydro tally counts planted cells of the 60", () => {
  expect(hydroTally(layout())).toEqual({ planted: 0, total: 60 });
  expect(hydroTally(layout("crops-planted"))).toEqual({ planted: 19, total: 60 });
});

test("a selection is the fewest place texts: cell ranges, then shelves, then zones", () => {
  expect(compressLabels(["EAST-HYDRO-3", "EAST-HYDRO-1", "EAST-HYDRO-2", "EAST-HYDRO-10", "SOUTH-FLOOR-2"])).toEqual(["EAST-HYDRO-1..3", "EAST-HYDRO-10", "SOUTH-FLOOR-2"]);
  expect(selectionPositions([zoneKey("center"), shelfKey("west_shelf_top"), cellKey("EAST-HYDRO-2"), cellKey("EAST-HYDRO-1")]))
    .toEqual(["EAST-HYDRO-1..2", "west_shelf_top", "center"]);
  expect(onlyCell(new Set([cellKey("EAST-HYDRO-7")]))).toBe("EAST-HYDRO-7");
  expect(onlyCell(new Set([shelfKey("south_floor")]))).toBeNull();
});

test("Shift selects a range on one shelf by number, in either direction, and across a rail", () => {
  const rack = hydro(layout());
  expect(cellRange(rack, "EAST-HYDRO-14", "EAST-HYDRO-5")).toHaveLength(10);
  expect(compressLabels(cellRange(rack, "EAST-HYDRO-5", "EAST-HYDRO-14"))).toEqual(["EAST-HYDRO-5..14"]);
  expect(cellRange(rack, "SOUTH-FLOOR-1", "EAST-HYDRO-5")).toEqual(["EAST-HYDRO-5"]);
});

test("the rack's one-tap groups: each rail, the top and bottom rows of 30, and the whole rack", () => {
  const groups = rackGroups(hydro(layout()));
  expect(groups.map((group) => group.label)).toEqual(["Rail A", "Rail B", "Rail C", "Top row", "Bottom row", expect.stringMatching(/^All /)]);
  const sizes = Object.fromEntries(groups.map((group) => [group.key, group.cells.length]));
  expect(sizes).toMatchObject({ "row-0": 30, "row-1": 30, all: 60 });
  expect(compressLabels(groups.find((group) => group.key === "row-0")!.cells)).toEqual(["EAST-HYDRO-1..30"]);
});

test("the plantings a selection touches, with the selected cells of each; a shelf-level planting by its shelf", () => {
  const locations = layout("crops-planted");
  const held = selectedPlantings(locations, new Set([cellKey("EAST-HYDRO-1"), cellKey("EAST-HYDRO-2"), cellKey("EAST-HYDRO-11"), cellKey("EAST-HYDRO-50")]));
  expect([...held.values()].map((item) => [item.crop, item.cells])).toEqual([["Lettuce", ["EAST-HYDRO-1", "EAST-HYDRO-2"]], ["Basil", ["EAST-HYDRO-11"]]]);
  const shelf = selectedPlantings(locations, new Set([shelfKey("west_shelf_top")]));
  expect([...shelf.values()].map((item) => [item.crop, item.cells])).toEqual([["Herbs", []]]);
  expect(legend(locations).map((item) => item.crop)).toEqual(expect.arrayContaining(["Lettuce", "Basil", "Vanda Orchids"]));
});
