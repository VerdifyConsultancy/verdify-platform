// SPDX-License-Identifier: MIT
/**
 * The Crops package (#1330): the location tree, the plantings and API-CROP-01 to -11 as the fixture API answers them.
 * The tree is the one migration 293 seeds: five zones, ten shelves (the hydro rack, the east, south and west wall
 * shelves, the south floor pots, the center floor and the hanging spots) and 96 cells with their layout, plus the
 * water lines that reach each shelf. The base world is production after 293: the Vandas hang in the center (a
 * shelf-level planting with an unknown count) and the ended plantings keep their cell history. The place parser
 * here reads the forms the page sends (labels, ranges, shelf and zone slugs) and a small part of the server's word
 * syntax; the server's `resolve_positions` is the one parser.
 *
 * Self-contained: world.mjs imports `baseCrops` from here, so this file imports nothing from it.
 */
const DAY = 86_400_000;
const iso = (ms) => new Date(ms).toISOString();
const ZONE = "America/Denver";
// One formatter each: building an Intl.DateTimeFormat per call made every world build slow.
const DATE_PARTS = new Intl.DateTimeFormat("en-US", { timeZone: ZONE, year: "numeric", month: "2-digit", day: "2-digit" });
const CLOCK = new Intl.DateTimeFormat("en-US", { timeZone: ZONE, hour: "2-digit", hourCycle: "h23", minute: "2-digit" });
const localDate = (ms) => {
  const part = Object.fromEntries(DATE_PARTS.formatToParts(new Date(ms)).map((item) => [item.type, item.value]));
  return `${part.year}-${part.month}-${part.day}`;
};
/** Local midnight of a `YYYY-MM-DD` day in America/Denver, as epoch ms (the offset is read at noon of that day). */
function localMidnight(date) {
  const noon = Date.parse(`${date}T12:00:00Z`);
  const shown = CLOCK.format(new Date(noon));
  const [hour, minute] = shown.split(":").map(Number);
  const offsetMs = noon - Date.parse(`${date}T${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}:00Z`);
  return Date.parse(`${date}T00:00:00Z`) + offsetMs;
}

// ---------------------------------------------------------------------------------------------------- the tree
const ZONES = [["center", "Center Greenhouse Zone"], ["east", "East Greenhouse Zone"], ["north", "North Greenhouse Zone"], ["south", "South Greenhouse Zone"], ["west", "West Greenhouse Zone"]];
const WALL_DRIP = [["L01", "Wall drip"], ["L02", "Wall drip, fertilizer"]];
const LINE_NAMES = { L01: "Wall drip", L02: "Wall drip fert", L04: "Center misters", L07: "South misters", L08: "South misters fert", L09: "West misters", L10: "West misters fert" };
const water = (pairs) => pairs.map(([line_id, note]) => ({ line_id, name: LINE_NAMES[line_id], note }));
/** slug, name, kind, tier, zone, wall, display_order, label_short, cells: [label, mount, group, row, col], water lines. */
const SHELVES = [
  ["center_hang", "Center Hang", "hang", null, "center", "center", 10, "Hanging", [1, 2].map((n) => [`CENTER-HANG-${n}`, "hanging_hook", null, 0, n]), water([["L04", "Center misters, overhead"]])],
  ["center_floor", "Center Floor", "floor", 0, "center", "center", 11, "Center floor", [1, 2, 3, 4].map((n) => [`CENTER-FLOOR-${n}`, "pot", null, 0, n]), water([["L04", "Center misters, overhead"]])],
  ["east_hydro", "East Hydro", "nft", null, "east", "east", 20, "Hydro",
    Array.from({ length: 60 }, (_, index) => { const n = index + 1; const col = ((n - 1) % 30) + 1; return [`EAST-HYDRO-${n}`, "nft_port", "ABC"[Math.floor((col - 1) / 10)], n > 30 ? 1 : 0, col]; }), []],
  ["east_shelf_top", "East Shelf (Top)", "shelf", 1, "east", "east", 21, "East top", [1, 2, 3].map((n) => [`EAST-SHELF-T${n}`, "shelf_slot", null, 0, n]), water(WALL_DRIP)],
  ["east_shelf_bottom", "East Shelf (Bottom)", "shelf", 0, "east", "east", 22, "East bottom", [1, 2, 3].map((n) => [`EAST-SHELF-B${n}`, "shelf_slot", null, 1, n]), water(WALL_DRIP)],
  ["south_shelf_top", "South Shelf (Top)", "shelf", 1, "south", "south", 30, "South top", [1, 2, 3, 4].map((n) => [`SOUTH-SHELF-T${n}`, "shelf_slot", n <= 2 ? "SE" : "SW", 0, n]),
    water([...WALL_DRIP, ["L07", "South wall misters"], ["L08", "South wall misters, fertilizer"]])],
  ["south_shelf_bottom", "South Shelf (Bottom)", "shelf", 0, "south", "south", 31, "South bottom", [1, 2, 3, 4].map((n) => [`SOUTH-SHELF-B${n}`, "shelf_slot", n <= 2 ? "SE" : "SW", 1, n]),
    water([...WALL_DRIP, ["L07", "South wall misters"], ["L08", "South wall misters, fertilizer"]])],
  ["south_floor", "South Floor", "floor", 0, "south", "south", 32, "South floor pots", [1, 2, 3, 4].map((n) => [`SOUTH-FLOOR-${n}`, "pot", null, 0, n]),
    water([...WALL_DRIP, ["L07", "South wall misters"], ["L08", "South wall misters, fertilizer"]])],
  ["west_shelf_top", "West Shelf (Top)", "shelf", 1, "west", "west", 40, "West top", [1, 2, 3, 4, 5, 6].map((n) => [`WEST-SHELF-T${n}`, "shelf_slot", null, 0, n]),
    water([...WALL_DRIP, ["L09", "West wall misters"], ["L10", "West wall misters, fertilizer"]])],
  ["west_shelf_bottom", "West Shelf (Bottom)", "shelf", 0, "west", "west", 41, "West bottom", [1, 2, 3, 4, 5, 6].map((n) => [`WEST-SHELF-B${n}`, "shelf_slot", null, 1, n]),
    water([...WALL_DRIP, ["L09", "West wall misters"], ["L10", "West wall misters, fertilizer"]])],
];
export const CATALOG = [
  ["basil", "Basil", "herb"], ["canna", "Canna Lilies", "ornamental"], ["cannabis", "Cannabis", "herb"], ["citrus", "Citrus", "fruit"], ["cucumbers", "Cucumbers", "vine"],
  ["herbs", "Herbs", "herb"], ["lettuce", "Lettuce", "leafy_green"], ["orchid", "Vanda Orchids", "ornamental"], ["peppers", "Peppers", "fruit"],
  ["strawberries", "Strawberries", "fruit"], ["tomatoes", "Tomatoes", "fruit"],
].map(([slug, name, category]) => ({ slug, name, category }));

const RECONCILE = { source: "system", name: "migration 293" };
/**
 * The crops part of the base world: the tree, production's plantings after 293 (ids as in production) and their
 * occupancy, events and harvests. Times are relative to `now`.
 */
export function baseCrops(now) {
  const daysAgo = (days) => localMidnight(localDate(now - days * DAY));
  const tree = {
    zones: ZONES.map(([slug, name]) => ({ slug, name })),
    shelves: SHELVES.map(([slug, name, kind, tier, zone, wall, display_order, label_short, , water_lines]) => ({ slug, name, kind, tier, zone, wall, display_order, label_short, water_lines })),
    cells: SHELVES.flatMap(([slug, , , , zone, , , , cells]) => cells.map(([label, mount_type, layout_group, layout_row, layout_col]) => ({ label, shelf: slug, zone, mount_type, layout_group, layout_row, layout_col }))),
  };
  const planting = (id, crop, slug, fields) => ({
    id, crop, variety: null, crop_catalog_slug: slug, count: null, stage: null, active: false, ended_at: null, end_reason: null, notes: null, recorded_by: RECONCILE, ...fields,
  });
  const ended = (days) => iso(daysAgo(days));
  const plantings = [
    planting(5, "Vanda Orchids", "orchid", { variety: "Mixed Vanda", planted_date: localDate(daysAgo(214)), active: true, stage: "flowering" }),
    planting(2, "Canna Lilies", "canna", { count: 1, planted_date: localDate(daysAgo(670)), ended_at: ended(150), end_reason: "removed" }),
    planting(3, "Lettuce", "lettuce", { count: 1, planted_date: localDate(daysAgo(246)), ended_at: ended(144), end_reason: "harvested" }),
    planting(8, "Strawberries", "strawberries", { count: 1, planted_date: localDate(daysAgo(215)), ended_at: ended(144), end_reason: "failed" }),
    planting(9, "Peppers", "peppers", { count: 1, planted_date: localDate(daysAgo(215)), ended_at: ended(0), end_reason: "reconciled" }),
  ];
  const occupancy = [
    { id: 1, planting_id: 5, zone: "center", shelf: "center_hang", cell: null, since: iso(daysAgo(214)), until: null, count: null },
    { id: 2, planting_id: 2, zone: "south", shelf: "south_floor", cell: "SOUTH-FLOOR-1", since: iso(daysAgo(670)), until: ended(150), count: 1 },
    { id: 3, planting_id: 3, zone: "east", shelf: "east_hydro", cell: "EAST-HYDRO-27", since: iso(daysAgo(246)), until: ended(144), count: 1 },
    { id: 4, planting_id: 8, zone: "east", shelf: "east_hydro", cell: "EAST-HYDRO-41", since: iso(daysAgo(215)), until: ended(144), count: 1 },
    { id: 5, planting_id: 9, zone: "east", shelf: "east_hydro", cell: "EAST-HYDRO-41", since: iso(daysAgo(144)), until: ended(0), count: 1 },
  ];
  const events = [
    { id: 1, planting_id: 5, ts: iso(daysAgo(214)), event_type: "planted", count: null, notes: null, source: "system", operator: "migration 293", detail: null },
    { id: 2, planting_id: 9, ts: ended(0), event_type: "reconciled", count: null, notes: "Ended as reconciled (#1329)", source: "system", operator: "migration 293", detail: { end_reason: "reconciled" } },
  ];
  const harvests = [{ id: 1, planting_id: 3, ts: ended(144), unit_count: 1, weight_kg: 0.2, notes: null, operator: "jason" }];
  return { tree, catalog: CATALOG.map((entry) => ({ ...entry })), plantings, occupancy, events, harvests };
}

// ---------------------------------------------------------------------------------------------------- helpers
const NUMBER = /(\d+)$/;
const cellOrder = (label) => { const match = NUMBER.exec(label); return match ? [label.slice(0, match.index), Number(match[1])] : [label, -1]; };
const byCell = (a, b) => { const [pa, na] = cellOrder(a); const [pb, nb] = cellOrder(b); return pa < pb ? -1 : pa > pb ? 1 : na - nb; };
/** `EAST-HYDRO-1..10, EAST-HYDRO-12`: consecutive numbers on one prefix as a range, as `compress_labels` writes it. */
export function compressLabels(labels) {
  const out = [];
  let run = [];
  const flush = () => { if (run.length) out.push(run.length === 1 ? `${run[0][0]}${run[0][1]}` : `${run[0][0]}${run[0][1]}..${run.at(-1)[1]}`); run = []; };
  for (const label of [...new Set(labels)].sort(byCell)) {
    const [prefix, number] = cellOrder(label);
    if (number < 0) { flush(); out.push(label); continue; }
    if (run.length && run.at(-1)[0] === prefix && run.at(-1)[1] + 1 === number) run.push([prefix, number]);
    else { flush(); run.push([prefix, number]); }
  }
  flush();
  return out.join(", ");
}
const openPlaces = (crops, id) => crops.occupancy.filter((row) => row.planting_id === id && row.until === null);
const placeOf = (row) => ({ zone: row.zone, shelf: row.shelf, cell: row.cell, since: row.since, until: row.until, count: row.count });
/** A planting as API-CROP-02 and the writes answer it. */
export function plantingBody(crops, row) {
  const places = openPlaces(crops, row.id).sort((a, b) => a.since.localeCompare(b.since) || a.id - b.id);
  const cells = [...new Set(places.filter((place) => place.cell).map((place) => place.cell))].sort(byCell);
  const plain = [...new Set(places.filter((place) => !place.cell).map((place) => place.shelf ?? place.zone))];
  const where = [cells.length ? compressLabels(cells) : "", ...plain].filter(Boolean).join(", ") || null;
  return {
    id: row.id, crop: row.crop, variety: row.variety, crop_catalog_slug: row.crop_catalog_slug, count: row.count, stage: row.stage, planted_date: row.planted_date,
    active: row.active, ended_at: row.ended_at, end_reason: row.end_reason, notes: row.notes, recorded_by: row.recorded_by,
    zones: [...new Set(places.map((place) => place.zone))], shelves: [...new Set(places.filter((place) => place.shelf).map((place) => place.shelf))], cells, where,
    places: places.map(placeOf),
  };
}
const occupant = (crops, row) => {
  const planting = crops.plantings.find((item) => item.id === row.planting_id);
  return { planting_id: row.planting_id, crop: planting.crop, variety: planting.variety, since: row.since, count: row.count };
};

// ---------------------------------------------------------------------------------------------------- refusals
export class CropRefusal extends Error {
  constructor(status, code, detail, fields = {}) { super(code); this.status = status; this.code = code; this.detail = detail; this.fields = fields; }
}
const refuse = (code, detail, field, fields = {}) => new CropRefusal(code === "not_found" ? 404 : code === "invalid_request" ? 400 : 422, code, detail, { field, ...fields });

// ---------------------------------------------------------------------------------------------------- the place parser
const KIND_WORDS = { hydro: "nft", hydroponic: "nft", hydroponics: "nft", nft: "nft", rack: "nft", rail: "nft", rails: "nft", floor: "floor", pot: "floor", pots: "floor", hang: "hang", hanging: "hang", hook: "hang", hooks: "hang", basket: "hang", baskets: "hang" };
const TIER_WORDS = { top: 1, upper: 1, bottom: 0, lower: 0 };
const FILLER = new Set(["cell", "cells", "slot", "slots", "position", "positions", "bay", "bays", "row", "rows", "wall", "zone", "the", "in", "on", "at", "of"]);
const numbers = (token) => {
  const [low, high = low] = token.split(/\s*(?:-|–|\.\.|to)\s*/).map(Number);
  const [a, b] = low <= high ? [low, high] : [high, low];
  if (b - a + 1 > 200) throw refuse("invalid_request", `'${token}' is more than 200 places`, "positions");
  return Array.from({ length: b - a + 1 }, (_, index) => a + index);
};
const cellTarget = (cell) => ({ zone: cell.zone, shelf: cell.shelf, cell: cell.label });
function words(tree, term, field) {
  const tokens = (term.match(/\d+\s*(?:-|–|\.\.|to)\s*\d+|\d+|[a-z_]+/gi) ?? []).map((token) => token.toLowerCase());
  const zones = new Set(tree.zones.map((zone) => zone.slug));
  let zone = null; let kind = null; let tier = null; let every = false; let shelfWord = false;
  const wanted = []; const rails = [];
  for (let index = 0; index < tokens.length; index += 1) {
    const token = tokens[index];
    if (/^\d/.test(token)) wanted.push(...numbers(token));
    else if (zones.has(token)) zone = token;
    else if ((token === "rail" || token === "rails") && /^[a-c]$/.test(tokens[index + 1] ?? "")) { kind = "nft"; rails.push(tokens[index + 1].toUpperCase()); index += 1; }
    else if (KIND_WORDS[token]) kind = KIND_WORDS[token];
    else if (token in TIER_WORDS) tier = TIER_WORDS[token];
    else if (token === "shelf" || token === "shelves") shelfWord = true;
    else if (["all", "every", "whole", "entire"].includes(token)) every = true;
    else if (!FILLER.has(token)) throw refuse("invalid_request", `'${term}': I don't know '${token}'. Use a cell label (EAST-HYDRO-12), a range (hydro 1-30, EAST-HYDRO-1..10), a rail (rail A top), a shelf (west top 1-3, south floor, hanging) or a zone (center)`, field);
  }
  if (kind === null && (tier !== null || shelfWord)) kind = "shelf";
  if (kind === null && !wanted.length) {
    if (zone === null) throw refuse("invalid_request", `'${term}' names no place`, field);
    if (!every) return [{ zone, shelf: null, cell: null }];
  }
  const shelves = tree.shelves.filter((shelf) => (zone === null || shelf.zone === zone) && (kind === null || shelf.kind === kind) && (kind !== "shelf" || tier === null || shelf.tier === tier));
  if (!shelves.length) throw refuse("invalid_request", `'${term}': no shelf or rack matches`, field);
  const row = kind === "nft" && tier !== null ? (tier === 1 ? 0 : 1) : null;
  let cells = tree.cells.filter((cell) => shelves.some((shelf) => shelf.slug === cell.shelf));
  if (rails.length) cells = cells.filter((cell) => rails.includes(cell.layout_group));
  if (row !== null) cells = cells.filter((cell) => cell.layout_row === row);
  if (wanted.length) {
    const number = (cell) => Number(NUMBER.exec(cell.label)?.[1]);
    cells = cells.filter((cell) => wanted.includes(number(cell)));
    const missing = wanted.filter((n) => !cells.some((cell) => number(cell) === n));
    if (missing.length) throw refuse("invalid_request", `'${term}': no cell numbered ${missing.slice(0, 5).join(", ")}`, field);
    const owners = [...new Set(cells.map((cell) => cell.shelf))].sort();
    if (owners.length > 1) throw refuse("ambiguous_position", `'${term}' could mean ${owners.join(", ")}. Name one of them; nothing was written`, field, { current: owners.join(", ") });
    return cells.map(cellTarget);
  }
  if (rails.length || row !== null || every) return cells.map(cellTarget);
  if (shelves.length > 1) {
    const owners = shelves.map((shelf) => shelf.slug).sort();
    throw refuse("ambiguous_position", `'${term}' could mean ${owners.join(", ")}. Name one of them; nothing was written`, field, { current: owners.join(", ") });
  }
  return [{ zone: shelves[0].zone, shelf: shelves[0].slug, cell: null }];
}
const RANGE = /^([A-Z][A-Z0-9_-]*?-?)(\d+)\s*\.\.\s*(?:([A-Z][A-Z0-9_-]*?-?))?(\d+)$/;
/** The places `terms` (a string or a list) names, in order and without repeats, as the server's resolver reads them. */
export function resolvePlaces(tree, terms, field = "positions") {
  const pieces = typeof terms === "string" ? [terms] : terms;
  if (!Array.isArray(pieces) || !pieces.length) throw refuse("invalid_request", "Name at least one place", field);
  const found = new Map();
  for (const piece of pieces) {
    if (typeof piece !== "string") throw refuse("invalid_request", "Each place is text", field);
    for (const term of piece.split(/\s*(?:,|;|\+|\band\b)\s*/i).map((part) => part.trim()).filter(Boolean)) {
      const upper = term.toUpperCase();
      const lower = term.toLowerCase();
      const cell = tree.cells.find((item) => item.label === upper);
      const shelf = tree.shelves.find((item) => item.slug === lower);
      let targets;
      if (cell) targets = [cellTarget(cell)];
      else if (shelf) targets = [{ zone: shelf.zone, shelf: shelf.slug, cell: null }];
      else if (tree.zones.some((zone) => zone.slug === lower)) targets = [{ zone: lower, shelf: null, cell: null }];
      else {
        const range = RANGE.exec(upper.replaceAll(" ", ""));
        if (range) {
          if (range[3] && range[3] !== range[1]) throw refuse("invalid_request", `'${term}' runs between two different racks or shelves`, field);
          const labels = numbers(`${range[2]}..${range[4]}`).map((n) => `${range[1]}${n}`);
          const missing = labels.filter((label) => !tree.cells.some((item) => item.label === label));
          if (missing.length) throw refuse("invalid_request", `No such cell: ${missing.slice(0, 5).join(", ")}`, field);
          targets = labels.map((label) => cellTarget(tree.cells.find((item) => item.label === label)));
        } else targets = words(tree, term, field);
      }
      for (const target of targets) {
        const key = `${target.zone}|${target.shelf ?? ""}|${target.cell ?? ""}`;
        if (!found.has(key)) found.set(key, target);
      }
    }
  }
  if (!found.size) throw refuse("invalid_request", "Name at least one place", field);
  return [...found.values()];
}
/** The open or later occupancy of the cells `targets` names at `at`, leaving out `exclude`. */
function conflictsOf(crops, targets, at, exclude = null) {
  const cells = new Set(targets.filter((target) => target.cell).map((target) => target.cell));
  return crops.occupancy.filter((row) => row.cell && cells.has(row.cell) && row.planting_id !== exclude && (row.until === null || Date.parse(row.until) > at))
    .sort((a, b) => byCell(a.cell, b.cell) || a.since.localeCompare(b.since))
    .map((row) => ({ cell: row.cell, planting_id: row.planting_id, crop: crops.plantings.find((item) => item.id === row.planting_id).crop, since: row.since, until: row.until }));
}
function occupied(conflicts, field) {
  const ids = [...new Set(conflicts.map((conflict) => conflict.planting_id))];
  const held = ids.map((id) => `${compressLabels(conflicts.filter((item) => item.planting_id === id).map((item) => item.cell))}: planting ${id} (${conflicts.find((item) => item.planting_id === id).crop})`).join("; ");
  return refuse("position_occupied", `Already planted: ${held}. Send replace to end those first; nothing was written`, field, { current: compressLabels(conflicts.map((item) => item.cell)) });
}
const whereOf = (targets) => {
  const cells = targets.filter((target) => target.cell).map((target) => target.cell).sort(byCell);
  return [cells.length ? compressLabels(cells) : "", ...targets.filter((target) => !target.cell).map((target) => target.shelf ?? target.zone)].filter(Boolean).join(", ");
};

// ---------------------------------------------------------------------------------------------------- reads
function locationsBody(world, { query }) {
  const { crops } = world;
  const zone = query.get("zone");
  if (zone !== null && !crops.tree.zones.some((item) => item.slug === zone)) throw refuse("unknown_target", `No zone '${zone}'`, "zone");
  const open = crops.occupancy.filter((row) => row.until === null).sort((a, b) => a.since.localeCompare(b.since) || a.id - b.id);
  let cellCount = 0; let occupiedCells = 0;
  const zones = crops.tree.zones.filter((item) => zone === null || item.slug === zone).map((item) => ({
    slug: item.slug, name: item.name,
    plantings: open.filter((row) => row.zone === item.slug && !row.shelf).map((row) => occupant(crops, row)),
    shelves: crops.tree.shelves.filter((shelf) => shelf.zone === item.slug).map((shelf) => {
      const cells = crops.tree.cells.filter((cell) => cell.shelf === shelf.slug).map((cell) => {
        const held = open.find((row) => row.cell === cell.label);
        return { label: cell.label, number: Number(NUMBER.exec(cell.label)?.[1]), mount_type: cell.mount_type, layout_group: cell.layout_group, layout_row: cell.layout_row, layout_col: cell.layout_col, occupant: held ? occupant(crops, held) : null };
      });
      cellCount += cells.length;
      occupiedCells += cells.filter((cell) => cell.occupant).length;
      return {
        slug: shelf.slug, name: shelf.name, kind: shelf.kind, tier: shelf.tier, wall: shelf.wall, display_order: shelf.display_order, label_short: shelf.label_short,
        water_lines: shelf.water_lines, plantings: open.filter((row) => row.shelf === shelf.slug && !row.cell).map((row) => occupant(crops, row)), cells,
      };
    }),
  }));
  return { zones, cell_count: cellCount, occupied_cells: occupiedCells, active_plantings: crops.plantings.filter((row) => row.active).length, server_time: iso(world.now) };
}
function plantingsBody(world, { query }) {
  const { crops } = world;
  const active = query.get("active");
  const zone = query.get("zone");
  const since = query.get("since");
  const limit = Number(query.get("limit") ?? 200);
  if (!Number.isInteger(limit) || limit < 1 || limit > 500) throw refuse("invalid_request", "limit must be 1-500", "limit");
  const rows = crops.plantings
    .filter((row) => active === "" || (active === "false" ? !row.active : row.active))
    .filter((row) => zone === null || crops.occupancy.some((place) => place.planting_id === row.id && place.zone === zone))
    .filter((row) => since === null || row.planted_date >= since || (row.ended_at !== null && localDate(Date.parse(row.ended_at)) >= since))
    .sort((a, b) => Number(b.active) - Number(a.active) || b.planted_date.localeCompare(a.planted_date) || b.id - a.id);
  return { items: rows.slice(0, limit).map((row) => plantingBody(crops, row)) };
}
const plantingOf = (crops, value, status = "not_found") => {
  const id = Number(value);
  const row = crops.plantings.find((item) => item.id === id);
  if (!row) throw refuse(status, `No planting ${value}`, "planting_id");
  return row;
};
function plantingDetail(world, { params }) {
  const { crops } = world;
  const row = plantingOf(crops, params.planting_id);
  const newest = (a, b) => (b.ts ?? "").localeCompare(a.ts ?? "") || b.id - a.id;
  return {
    ...plantingBody(crops, row),
    history: crops.occupancy.filter((place) => place.planting_id === row.id).sort((a, b) => a.since.localeCompare(b.since) || a.id - b.id).map(placeOf),
    events: crops.events.filter((event) => event.planting_id === row.id).sort(newest).map(({ planting_id: _id, ...event }) => event),
    harvests: crops.harvests.filter((harvest) => harvest.planting_id === row.id).sort(newest).map(({ planting_id: _id, ...harvest }) => harvest),
  };
}
function positionHistory(world, { params }) {
  const { crops } = world;
  const cell = crops.tree.cells.find((item) => item.label === params.label.toUpperCase());
  if (!cell) throw refuse("not_found", `No cell '${params.label}'`, "label");
  const items = crops.occupancy.filter((row) => row.cell === cell.label).sort((a, b) => b.since.localeCompare(a.since) || b.id - a.id)
    .sort((a, b) => Number(a.until !== null) - Number(b.until !== null))
    .map((row) => { const planting = crops.plantings.find((item) => item.id === row.planting_id); return { planting_id: row.planting_id, crop: planting.crop, variety: planting.variety, since: row.since, until: row.until, count: row.count }; });
  return { label: cell.label, zone: cell.zone, shelf: cell.shelf, items };
}
function moment(world, value, field) {
  if (value === undefined || value === null) return world.now;
  if (typeof value !== "string") throw refuse("invalid_request", `${field} is a date or a date-time`, field);
  const ms = /^\d{4}-\d\d-\d\d$/.test(value) ? localMidnight(value) : Date.parse(value);
  if (!Number.isFinite(ms) || (!/^\d{4}-\d\d-\d\d$/.test(value) && !/(Z|[+-]\d\d:\d\d)$/.test(value))) throw refuse("invalid_request", `${field} needs its UTC offset`, field);
  if (ms > world.now + 60_000) throw refuse("invalid_request", `${field} is in the future`, field);
  return ms;
}
function resolveBody(world, { query }) {
  const { crops } = world;
  const q = query.get("q");
  if (!q || q.length > 500) throw refuse("invalid_request", "q is 1-500 characters", "q");
  const at = moment(world, query.get("at") ?? undefined, "at");
  const exclude = query.get("planting") === null ? null : Number(query.get("planting"));
  const targets = resolvePlaces(crops.tree, q, "q");
  const cells = targets.filter((target) => target.cell).map((target) => target.cell).sort(byCell);
  return { places: targets, cells, where: whereOf(targets), at: iso(at), conflicts: conflictsOf(crops, targets, at, exclude) };
}
export const CROP_READS = {
  "API-CROP-01": locationsBody,
  "API-CROP-02": plantingsBody,
  "API-CROP-03": plantingDetail,
  "API-CROP-04": positionHistory,
  "API-CROP-05": (world) => ({ items: world.crops.catalog }),
  "API-CROP-06": resolveBody,
};

// ---------------------------------------------------------------------------------------------------- writes
const STAGES = ["seed", "germination", "seedling", "vegetative", "flowering", "fruiting", "harvest", "cleared"];
const body = (value) => {
  if (value === undefined || value === null) return {};
  if (typeof value !== "object" || Array.isArray(value)) throw refuse("invalid_request", "The body must be a JSON object", "body");
  return value;
};
const optionalText = (value, field, max = 500) => {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || value.length > max) throw refuse("invalid_request", `${field} must be text of at most ${max} characters`, field);
  return value;
};
const optionalCount = (value, field, max) => {
  if (value === undefined || value === null) return null;
  if (!Number.isInteger(value) || value < 0 || value > max) throw refuse("invalid_request", `${field} must be a whole number from 0 to ${max}`, field);
  return value;
};
const placeList = (value, field) => {
  const list = typeof value === "string" ? [value] : value;
  if (!Array.isArray(list) || list.length < 1 || list.length > 40) throw refuse("invalid_request", `${field} lists 1 to 40 places`, field);
  return list;
};
const nextOf = (rows) => rows.reduce((max, row) => Math.max(max, row.id), 0) + 1;
const actorOf = (world) => ({ source: "dashboard", name: world.me.username });
function logged(world, action, target, detail, reason, key) {
  const id = world.activity.reduce((max, row) => Math.max(max, Number(row.id) || 0), 0) + 1;
  world.activity.push({ id, ts: iso(world.now), actor: actorOf(world), action, target, detail, reason: reason ?? null, state: null, confirmed_at: null, experiment_id: null, request_id: key ?? null });
}
function close(crops, rows, at) {
  for (const row of rows) row.until = iso(at);
}
function endPlanting(crops, row, at, reason) {
  close(crops, openPlaces(crops, row.id), at);
  Object.assign(row, { active: false, ended_at: iso(at), end_reason: reason });
}
/** `replace`: ends each occupant's open range on a requested cell; an occupant with no place left ends as removed. */
function vacate(crops, conflicts, at, field) {
  for (const conflict of conflicts) {
    if (conflict.until !== null || Date.parse(conflict.since) >= at) throw refuse("position_occupied", `${conflict.cell} holds planting ${conflict.planting_id} (${conflict.crop}) from ${conflict.since}, which replace cannot shorten; choose a later time`, field, { current: conflict.cell });
  }
  for (const id of new Set(conflicts.map((conflict) => conflict.planting_id))) {
    const cells = conflicts.filter((conflict) => conflict.planting_id === id).map((conflict) => conflict.cell);
    close(crops, openPlaces(crops, id).filter((row) => cells.includes(row.cell)), at);
    if (!openPlaces(crops, id).length) endPlanting(crops, crops.plantings.find((item) => item.id === id), at, "removed");
  }
}
function open(crops, id, targets, at, count) {
  for (const target of targets) crops.occupancy.push({ id: nextOf(crops.occupancy), planting_id: id, ...target, since: iso(at), until: null, count: target.cell ? count : null });
}
const event = (crops, id, at, event_type, fields, world) => crops.events.push({ id: nextOf(crops.events), planting_id: id, ts: iso(at), event_type, count: null, notes: null, source: "dashboard", operator: world.me.username, detail: null, ...fields });
function activeOf(crops, value) {
  const row = plantingOf(crops, value, "unknown_target");
  if (!row.active) throw refuse("planting_ended", `Planting ${row.id} (${row.crop}) ended ${row.ended_at}`, "planting_id");
  return row;
}
/** Each write: what it does to a world, so the fixture API replays it on every later request's world. */
const PERFORM = {
  "API-CROP-07": (world, { input, key }) => {
    const { crops } = world;
    const crop = input.crop;
    if (typeof crop !== "string" || !crop.trim() || crop.length > 200) throw refuse("invalid_request", "crop is 1-200 characters", "crop");
    const positions = placeList(input.positions, "positions");
    const at = moment(world, input.planted_at, "planted_at");
    const variety = optionalText(input.variety, "variety", 200);
    const count = optionalCount(input.count, "count", 100_000);
    const perCell = optionalCount(input.count_per_cell, "count_per_cell", 1000);
    if (input.stage !== undefined && input.stage !== null && !STAGES.includes(input.stage)) throw refuse("invalid_request", "stage is not one of the stages", "stage");
    const targets = resolvePlaces(crops.tree, positions);
    const conflicts = conflictsOf(crops, targets, at);
    if (conflicts.length && input.replace !== true) throw occupied(conflicts, "positions");
    if (conflicts.length) vacate(crops, conflicts, at, "positions");
    const wanted = crop.trim().toLowerCase();
    const entry = crops.catalog.find((item) => [item.slug, item.name.toLowerCase()].some((name) => name === wanted || name.replace(/s$/, "") === wanted.replace(/s$/, "")));
    const id = Math.max(nextOf(crops.plantings), 16);
    crops.plantings.push({
      id, crop: entry?.name ?? crop.trim(), variety, crop_catalog_slug: entry?.slug ?? null, count, stage: input.stage ?? "seed", planted_date: localDate(at), active: true,
      ended_at: null, end_reason: null, notes: optionalText(input.notes, "notes", 2000), recorded_by: actorOf(world),
    });
    open(crops, id, targets, at, perCell);
    event(crops, id, at, "planted", { count }, world);
    const stored = plantingBody(crops, crops.plantings.at(-1));
    logged(world, "planting.create", String(id), { ref: { table: "crops", id }, crop: stored.crop, variety, where: stored.where, cells: stored.cells, planted_at: iso(at) }, input.reason, key);
    return { status: 201, body: stored };
  },
  "API-CROP-08": (world, { params, input, key }) => {
    const { crops } = world;
    const row = activeOf(crops, params.planting_id);
    const to = resolvePlaces(crops.tree, placeList(input.to_positions, "to_positions"), "to_positions");
    const at = moment(world, input.moved_at, "moved_at");
    const rows = openPlaces(crops, row.id);
    let leaving = rows;
    if (input.from_positions !== undefined && input.from_positions !== null) {
      const from = resolvePlaces(crops.tree, placeList(input.from_positions, "from_positions"), "from_positions");
      leaving = from.map((target) => {
        const match = rows.find((item) => item.zone === target.zone && item.shelf === target.shelf && item.cell === target.cell);
        if (!match) throw refuse("invalid_request", `The planting is not at ${target.cell ?? target.shelf ?? target.zone}`, "from_positions");
        return match;
      });
    }
    const staying = rows.filter((item) => !leaving.includes(item) && item.cell).map((item) => item.cell);
    if (to.some((target) => target.cell && staying.includes(target.cell))) throw refuse("invalid_request", "The planting already holds a destination cell", "to_positions");
    const conflicts = conflictsOf(crops, to, at, row.id);
    if (conflicts.length && input.replace !== true) throw occupied(conflicts, "to_positions");
    if (conflicts.length) vacate(crops, conflicts, at, "to_positions");
    const counts = [...new Set(leaving.filter((item) => item.cell).map((item) => item.count))];
    const from = leaving.map((item) => item.cell ?? item.shelf ?? item.zone);
    close(crops, leaving, at);
    open(crops, row.id, to, at, counts.length === 1 ? counts[0] : null);
    event(crops, row.id, at, "moved", { notes: optionalText(input.reason, "reason"), detail: { from, to: to.map((target) => target.cell ?? target.shelf ?? target.zone) } }, world);
    logged(world, "planting.move", String(row.id), { ref: { table: "crops", id: row.id }, moved_at: iso(at), from, to: to.map((target) => target.cell ?? target.shelf ?? target.zone) }, input.reason, key);
    return { status: 200, body: plantingBody(crops, row) };
  },
  "API-CROP-09": (world, { params, input, key }) => {
    const { crops } = world;
    const row = activeOf(crops, params.planting_id);
    const at = moment(world, input.harvested_at, "harvested_at");
    const count = optionalCount(input.count, "count", 100_000);
    if (input.weight_kg !== undefined && input.weight_kg !== null && (typeof input.weight_kg !== "number" || input.weight_kg < 0)) throw refuse("invalid_request", "weight_kg is a number of at least 0", "weight_kg");
    const rows = openPlaces(crops, row.id);
    let clearing = input.final === true ? rows : [];
    if (input.positions && input.final !== true) {
      const cells = resolvePlaces(crops.tree, placeList(input.positions, "positions")).map((target) => target.cell);
      clearing = rows.filter((item) => item.cell && cells.includes(item.cell));
      if (clearing.length !== cells.length) throw refuse("invalid_request", "The planting is not at every place named", "positions");
    }
    close(crops, clearing, at);
    const ended = input.final === true || (clearing.length > 0 && clearing.length === rows.length);
    const id = nextOf(crops.harvests);
    const notes = optionalText(input.notes, "notes", 2000);
    crops.harvests.push({ id, planting_id: row.id, ts: iso(at), unit_count: count, weight_kg: input.weight_kg ?? null, notes, operator: world.me.username });
    event(crops, row.id, at, "harvested", { count, notes, detail: { harvest_id: id, cleared: clearing.map((item) => item.cell ?? item.shelf ?? item.zone), final: ended } }, world);
    if (ended) endPlanting(crops, row, at, "harvested");
    logged(world, "planting.harvest", String(row.id), { ref: { table: "crops", id: row.id }, harvested_at: iso(at), count, final: ended }, input.reason, key);
    return { status: 200, body: plantingBody(crops, row) };
  },
  "API-CROP-10": (world, { params, input, key }) => {
    const { crops } = world;
    const row = activeOf(crops, params.planting_id);
    if (!["failed", "removed", "reconciled"].includes(input.end_reason)) throw refuse("invalid_request", "end_reason is failed, removed or reconciled", "end_reason");
    const at = moment(world, input.at, "at");
    const note = optionalText(input.note, "note", 2000);
    const places = openPlaces(crops, row.id).map((item) => item.cell ?? item.shelf ?? item.zone);
    if (note || input.end_reason === "reconciled") event(crops, row.id, at, input.end_reason === "reconciled" ? "reconciled" : "note", { notes: note, detail: { end_reason: input.end_reason, places } }, world);
    endPlanting(crops, row, at, input.end_reason);
    logged(world, "planting.end", String(row.id), { ref: { table: "crops", id: row.id }, end_reason: input.end_reason, at: iso(at), places }, input.reason, key);
    return { status: 200, body: plantingBody(crops, row) };
  },
  "API-CROP-11": (world, { params, input, key }) => {
    const { crops } = world;
    const row = plantingOf(crops, params.planting_id, "unknown_target");
    const fields = ["variety", "count", "stage", "notes", "expected_harvest"].filter((field) => Object.hasOwn(input, field));
    if (!fields.length) throw refuse("invalid_request", "Send at least one field to change", "body");
    const change = {};
    if (Object.hasOwn(input, "variety")) change.variety = optionalText(input.variety, "variety", 200);
    if (Object.hasOwn(input, "count")) change.count = optionalCount(input.count, "count", 100_000);
    if (Object.hasOwn(input, "notes")) change.notes = optionalText(input.notes, "notes", 2000);
    if (Object.hasOwn(input, "stage")) {
      if (input.stage !== null && !STAGES.includes(input.stage)) throw refuse("invalid_request", "stage is not one of the stages", "stage");
      change.stage = input.stage;
    }
    const before = Object.fromEntries(Object.keys(change).map((field) => [field, row[field]]));
    Object.assign(row, change);
    logged(world, "planting.update", String(row.id), { ref: { table: "crops", id: row.id }, before, after: change }, input.reason, key);
    return { status: 200, body: plantingBody(crops, row) };
  },
};
/** A crop write: its answer on this world and the effect that replays it. A refusal throws `CropRefusal`. */
export function cropWrite(operationId, world, { params, body: raw, key }) {
  const input = body(raw);
  // The answer comes from a copy: a fixture set answers every operation on one world, and only an effect changes it.
  const scratch = { ...world, crops: structuredClone(world.crops), activity: structuredClone(world.activity) };
  const answer = PERFORM[operationId](scratch, { params, input, key });
  return { ...answer, effect: (next) => { PERFORM[operationId](next, { params, input, key }); } };
}
export const CROP_WRITES = Object.keys(PERFORM);

// ---------------------------------------------------------------------------------------------------- named fixtures
/**
 * The Crops package's named fixtures. `crops-planted`: a re-plant under way, for the map, history and the conflict
 * checks: lettuce on rail A (EAST-HYDRO-1..10, top row), basil on rail B top (11..15), strawberries on four bottom
 * cells, a shelf-level herb planting on West top, a tomato in a south floor pot and an ended basil batch in history.
 */
export const FIXTURES = {
  "crops-planted": (world) => {
    const { crops } = world;
    const at = (days) => localMidnight(localDate(world.now - days * DAY)) + 9 * 3_600_000;
    const add = (id, crop, slug, variety, days, places, fields = {}) => {
      crops.plantings.push({
        id, crop, variety, crop_catalog_slug: slug, count: fields.count ?? null, stage: "vegetative", planted_date: localDate(at(days)), active: fields.active ?? true,
        ended_at: fields.ended_at ?? null, end_reason: fields.end_reason ?? null, notes: fields.notes ?? null, recorded_by: { source: "dashboard", name: "jason" },
      });
      for (const place of places) crops.occupancy.push({ id: nextOf(crops.occupancy), planting_id: id, ...place, since: iso(at(days)), until: fields.ended_at ?? null, count: place.cell ? 1 : null });
      crops.events.push({ id: nextOf(crops.events), planting_id: id, ts: iso(at(days)), event_type: "planted", count: fields.count ?? null, notes: null, source: "dashboard", operator: "jason", detail: null });
    };
    const hydro = (numbers) => numbers.map((n) => ({ zone: "east", shelf: "east_hydro", cell: `EAST-HYDRO-${n}` }));
    add(16, "Lettuce", "lettuce", "Butterhead", 12, hydro([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]), { count: 10 });
    add(17, "Basil", "basil", "Genovese", 5, hydro([11, 12, 13, 14, 15]), { count: 5 });
    add(18, "Strawberries", "strawberries", "Albion", 30, hydro([31, 32, 33, 34]), { count: 4 });
    add(19, "Herbs", "herbs", "Thyme and oregano", 20, [{ zone: "west", shelf: "west_shelf_top", cell: null }], { count: 6, notes: "Three planters" });
    add(20, "Tomatoes", "tomatoes", "Sungold", 40, [{ zone: "south", shelf: "south_floor", cell: "SOUTH-FLOOR-2" }], { count: 1 });
    add(21, "Basil", "basil", "Thai", 60, hydro([21, 22]), { count: 2, active: false, ended_at: iso(at(25)), end_reason: "harvested" });
  },
};
