// SPDX-License-Identifier: MIT
/**
 * API-CROP-01 to -11 (#1327) from a fixture world: the location tree as migration 293 leaves it (60 hydro cells on
 * rails A-C, the wall shelves by bay, the floor pots and the hanging hooks) and the one active planting, the Vandas,
 * hanging at center/hanging with an unknown count. The resolver here reads only labels, label ranges, `hydro N-M` and
 * shelf or zone slugs; the server's parser (verdify_services.crops) reads more. Nothing here steers anything.
 */
import { URLSearchParams } from "node:url";
import { JASON, iso } from "./world.mjs";

const ZONES = [["center", "Center Greenhouse Zone"], ["east", "East Greenhouse Zone"], ["north", "North Greenhouse Zone"], ["south", "South Greenhouse Zone"], ["west", "West Greenhouse Zone"]];
/** slug, name, kind, tier, zone, wall, display order, short label, cell prefix and count, water lines */
const SHELVES = [
  ["center_hang", "Center hanging", "hang", null, "center", "center", 10, "Hanging", "CENTER-HANG-", 2, ["L04"]],
  ["center_floor", "Center floor", "floor", 0, "center", "center", 11, "Center floor", "CENTER-FLOOR-", 4, ["L04"]],
  ["east_hydro", "East hydro rack", "nft", null, "east", "east", 20, "Hydro", "EAST-HYDRO-", 60, []],
  ["east_shelf_top", "East shelf top", "shelf", 1, "east", "east", 21, "East top", "EAST-SHELF-T", 3, ["L01", "L02"]],
  ["east_shelf_bottom", "East shelf bottom", "shelf", 0, "east", "east", 22, "East bottom", "EAST-SHELF-B", 3, ["L01", "L02"]],
  ["south_shelf_top", "South shelf top", "shelf", 1, "south", "south", 30, "South top", "SOUTH-SHELF-T", 4, ["L01", "L02", "L07", "L08"]],
  ["south_shelf_bottom", "South shelf bottom", "shelf", 0, "south", "south", 31, "South bottom", "SOUTH-SHELF-B", 4, ["L01", "L02", "L07", "L08"]],
  ["south_floor", "South floor", "floor", 0, "south", "south", 32, "South floor pots", "SOUTH-FLOOR-", 4, ["L01", "L02", "L07", "L08"]],
  ["west_shelf_top", "West shelf top", "shelf", 1, "west", "west", 40, "West top", "WEST-SHELF-T", 6, ["L01", "L02", "L09", "L10"]],
  ["west_shelf_bottom", "West shelf bottom", "shelf", 0, "west", "west", 41, "West bottom", "WEST-SHELF-B", 6, ["L01", "L02", "L09", "L10"]],
];
const LINE_NAMES = { L01: "Wall drip", L02: "Wall drip, fertilized", L04: "Center mister", L07: "South mister", L08: "South mister, fertilized", L09: "West mister", L10: "West mister, fertilized" };
const CATALOG = [["basil", "Basil", "herb"], ["lettuce", "Lettuce", "leafy_green"], ["orchid", "Orchid", "ornamental"], ["strawberries", "Strawberries", "fruit"], ["tomatoes", "Tomatoes", "fruit"]];

function cellsOf([slug, , kind, , , , , , prefix, count]) {
  return Array.from({ length: count }, (_, index) => {
    const number = index + 1;
    const hydro = kind === "nft";
    const col = hydro ? ((number - 1) % 30) + 1 : number;
    return {
      label: `${prefix}${number}`, number, shelf: slug, mount_type: hydro ? "nft_port" : kind === "hang" ? "hook" : "pot",
      layout_group: hydro ? "ABC"[Math.floor((col - 1) / 10)] : null, layout_row: hydro ? Number(number > 30) : prefix.includes("-B") ? 1 : 0, layout_col: col,
    };
  });
}
const CELLS = SHELVES.flatMap(cellsOf);
const shelfOf = (slug) => SHELVES.find((row) => row[0] === slug);
const cellOf = (label) => CELLS.find((cell) => cell.label === label);
const numberOf = (label) => Number(/(\d+)$/.exec(label)?.[1] ?? NaN);

/** The world's plantings, seeded on first use; writes change them through their effects. */
export function cropsOf(world) {
  world.crops ??= {
    plantings: [{
      id: 5, crop: "Vanda Orchids", variety: null, crop_catalog_slug: "orchid", count: null, stage: "flowering", planted_date: "2026-03-01", active: true,
      ended_at: null, end_reason: null, notes: "Several, hanging in the center", recorded_by: JASON,
      places: [{ zone: "center", shelf: "center_hang", cell: null, since: "2026-03-01T07:00:00.000Z", until: null, count: null }],
    }],
    events: [],
    harvests: [],
  };
  return world.crops;
}

/** Labels as the server shows them: consecutive numbers on one prefix as a range. */
export function compress(labels) {
  const sorted = [...new Set(labels)].sort((a, b) => a.replace(/\d+$/, "").localeCompare(b.replace(/\d+$/, "")) || numberOf(a) - numberOf(b));
  const out = [];
  for (let i = 0; i < sorted.length;) {
    const prefix = sorted[i].replace(/\d+$/, "");
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1].replace(/\d+$/, "") === prefix && numberOf(sorted[j + 1]) === numberOf(sorted[j]) + 1) j += 1;
    out.push(i === j ? sorted[i] : `${sorted[i]}..${numberOf(sorted[j])}`);
    i = j + 1;
  }
  return out.join(", ");
}

const open = (planting) => planting.places.filter((place) => place.until === null);
function view(planting) {
  const places = open(planting);
  const cells = places.filter((place) => place.cell).map((place) => place.cell).sort((a, b) => numberOf(a) - numberOf(b));
  const plain = places.filter((place) => !place.cell).map((place) => place.shelf ?? place.zone);
  return {
    ...planting, zones: [...new Set(places.map((place) => place.zone))], shelves: [...new Set(places.map((place) => place.shelf).filter(Boolean))], cells,
    where: [cells.length ? compress(cells) : "", ...plain].filter(Boolean).join(", ") || null, places,
  };
}
const occupant = (planting, place) => ({ planting_id: planting.id, crop: planting.crop, variety: planting.variety, since: place.since, count: place.count });

export function makeCrops({ refuse, invalid, object, activity }) {
  function resolve(terms, field = "positions") {
    const list = (Array.isArray(terms) ? terms : [terms]).flatMap((term) => String(term).split(","));
    const targets = [];
    for (const raw of list.map((term) => term.trim()).filter(Boolean)) {
      const upper = raw.toUpperCase();
      const hydro = /^hydro\s+(\d+)(?:\s*-\s*(\d+))?$/i.exec(raw);
      const range = /^([A-Z-]+?)(\d+)\.\.(\d+)$/.exec(upper);
      if (cellOf(upper)) targets.push(cellOf(upper));
      else if (hydro || range) {
        const [prefix, low, high] = hydro ? ["EAST-HYDRO-", Number(hydro[1]), Number(hydro[2] ?? hydro[1])] : [range[1], Number(range[2]), Number(range[3])];
        for (let n = low; n <= high; n += 1) {
          if (!cellOf(`${prefix}${n}`)) throw invalid(`No such cell: ${prefix}${n}`, field);
          targets.push(cellOf(`${prefix}${n}`));
        }
      } else if (shelfOf(raw.toLowerCase())) targets.push({ shelf: raw.toLowerCase() });
      else if (ZONES.some(([slug]) => slug === raw.toLowerCase())) targets.push({ zone: raw.toLowerCase() });
      else throw invalid(`'${raw}': use a cell label, a range (hydro 1-30, EAST-HYDRO-1..10), a shelf or a zone`, field);
    }
    if (!targets.length) throw invalid("Name at least one place", field);
    return targets.map((target) => {
      if (target.zone) return { zone: target.zone, shelf: null, cell: null };
      const shelf = shelfOf(target.shelf);
      return { zone: shelf[4], shelf: shelf[0], cell: target.label ?? null };
    });
  }
  const conflicts = (world, places, exclude = null) => cropsOf(world).plantings.flatMap((planting) => (planting.id === exclude ? [] : open(planting)
    .filter((place) => place.cell && places.some((wanted) => wanted.cell === place.cell))
    .map((place) => ({ cell: place.cell, planting_id: planting.id, crop: planting.crop, since: place.since, until: null }))));
  const occupied = (found, field) => refuse(422, "position_occupied", "Position occupied", `Already planted: ${compress(found.map((item) => item.cell))}. Send replace to end those first; nothing was written`, { field, current: compress(found.map((item) => item.cell)) });
  const plantingOf = (world, id, active = true) => {
    const planting = cropsOf(world).plantings.find((item) => item.id === Number(id));
    if (!planting) throw refuse(422, "unknown_target", "Unknown target", `No planting ${id}`, { field: "planting_id" });
    if (active && !planting.active) throw refuse(422, "planting_ended", "Planting ended", `Planting ${id} (${planting.crop}) has ended`, { field: "planting_id" });
    return planting;
  };
  const vacate = (next, found, at) => {
    for (const item of found) {
      const planting = cropsOf(next).plantings.find((row) => row.id === item.planting_id);
      for (const place of open(planting)) if (place.cell === item.cell) place.until = at;
      if (!open(planting).length) Object.assign(planting, { active: false, ended_at: at, end_reason: "removed" });
    }
  };
  const log = (next, request, action, target, detail) => activity(next, { ts: iso(next.now), actor: JASON, action, target: String(target), detail, reason: request.reason ?? null, request_id: request.key ?? null });

  function locations(world, { query }) {
    const wanted = query.get("zone");
    const plantings = cropsOf(world).plantings;
    const holding = (test) => plantings.flatMap((planting) => open(planting).filter(test).map((place) => occupant(planting, place)));
    const zones = ZONES.filter(([slug]) => !wanted || slug === wanted).map(([slug, name]) => ({
      slug, name, plantings: holding((place) => place.zone === slug && !place.shelf),
      shelves: SHELVES.filter((row) => row[4] === slug).map(([shelf, label, kind, tier, , wall, order, short, , , lines]) => ({
        slug: shelf, name: label, kind, tier, wall, display_order: order, label_short: short,
        water_lines: lines.map((line) => ({ line_id: line, name: LINE_NAMES[line], note: null })),
        plantings: holding((place) => place.shelf === shelf && !place.cell),
        cells: CELLS.filter((cell) => cell.shelf === shelf).map(({ shelf: _shelf, ...cell }) => ({ ...cell, occupant: holding((place) => place.cell === cell.label)[0] ?? null })),
      })),
    }));
    if (wanted && !zones.length) throw refuse(422, "unknown_target", "Unknown target", `No zone '${wanted}'`, { field: "zone" });
    const cells = zones.flatMap((zone) => zone.shelves.flatMap((shelf) => shelf.cells));
    return { zones, cell_count: cells.length, occupied_cells: cells.filter((cell) => cell.occupant).length, active_plantings: plantings.filter((item) => item.active).length, server_time: iso(world.now) };
  }
  function list(world, { query }) {
    const active = query.get("active");
    const zone = query.get("zone");
    const items = cropsOf(world).plantings
      .filter((item) => active === "" || active === null ? item.active : active === "false" ? !item.active : item.active)
      .filter((item) => !zone || item.places.some((place) => place.zone === zone))
      .sort((a, b) => Number(b.active) - Number(a.active) || b.planted_date.localeCompare(a.planted_date) || b.id - a.id);
    return { items: items.map(view) };
  }
  function detail(world, { params }) {
    const planting = cropsOf(world).plantings.find((item) => item.id === Number(params.planting_id));
    if (!planting) throw refuse(404, "not_found", "Not found", `No planting ${params.planting_id}`, { field: "planting_id" });
    const { events, harvests } = cropsOf(world);
    return {
      ...view(planting), history: planting.places, events: events.filter((event) => event.planting_id === planting.id).map(({ planting_id: _id, ...event }) => event).reverse(),
      harvests: harvests.filter((row) => row.planting_id === planting.id).map(({ planting_id: _id, ...row }) => row).reverse(),
    };
  }
  function history(world, { params }) {
    const cell = cellOf(params.label.toUpperCase());
    if (!cell) throw refuse(404, "not_found", "Not found", `No cell '${params.label}'`, { field: "label" });
    const items = cropsOf(world).plantings.flatMap((planting) => planting.places.filter((place) => place.cell === cell.label)
      .map((place) => ({ planting_id: planting.id, crop: planting.crop, variety: planting.variety, since: place.since, until: place.until, count: place.count })));
    items.sort((a, b) => Number(a.until !== null) - Number(b.until !== null) || b.since.localeCompare(a.since));
    return { label: cell.label, zone: shelfOf(cell.shelf)[4], shelf: cell.shelf, items };
  }
  function preview(world, { query }) {
    const q = query.get("q");
    if (!q) throw invalid("q is required", "q");
    const at = query.get("at") ?? iso(world.now);
    const places = resolve(q);
    const cells = places.filter((place) => place.cell).map((place) => place.cell);
    const plain = places.filter((place) => !place.cell).map((place) => place.shelf ?? place.zone);
    const exclude = query.get("planting") ? Number(query.get("planting")) : null;
    return { places, cells, where: [cells.length ? compress(cells) : "", ...plain].filter(Boolean).join(", "), at, conflicts: conflicts(world, places, exclude) };
  }

  function plant(world, request) {
    const body = object(request.body);
    if (typeof body.crop !== "string" || !body.crop) throw invalid("crop is required", "crop");
    const places = resolve(body.positions ?? []);
    const found = conflicts(world, places);
    if (found.length && !body.replace) throw occupied(found, "positions");
    const at = iso(world.now);
    const id = Math.max(0, ...cropsOf(world).plantings.map((item) => item.id)) + 1;
    const entry = CATALOG.find(([slug, name]) => [slug, name.toLowerCase()].includes(body.crop.toLowerCase()));
    const planting = {
      id, crop: entry ? entry[1] : body.crop, variety: body.variety ?? null, crop_catalog_slug: entry ? entry[0] : null, count: body.count ?? null,
      stage: body.stage ?? "seed", planted_date: at.slice(0, 10), active: true, ended_at: null, end_reason: null, notes: body.notes ?? null, recorded_by: JASON,
      places: places.map((place) => ({ ...place, since: at, until: null, count: place.cell ? (body.count_per_cell ?? null) : null })),
    };
    const effect = (next) => {
      vacate(next, conflicts(next, places), iso(next.now));
      cropsOf(next).plantings.push(structuredClone(planting));
      log(next, { ...request, reason: body.reason }, "planting.create", id, { ref: { table: "crops", id }, crop: planting.crop, where: view(planting).where });
    };
    return { status: 201, body: view(planting), effect };
  }
  function move(world, request) {
    const body = object(request.body);
    const planting = structuredClone(plantingOf(world, request.params.planting_id));
    const places = resolve(body.to_positions ?? [], "to_positions");
    const found = conflicts(world, places, planting.id);
    if (found.length && !body.replace) throw occupied(found, "to_positions");
    const at = iso(world.now);
    const apply = (target) => {
      for (const place of open(target)) place.until = at;
      target.places.push(...places.map((place) => ({ ...place, since: at, until: null, count: null })));
    };
    apply(planting);
    const effect = (next) => {
      vacate(next, conflicts(next, places, planting.id), iso(next.now));
      apply(plantingOf(next, planting.id));
      log(next, { ...request, reason: body.reason }, "planting.move", planting.id, { ref: { table: "crops", id: planting.id }, to: places.map((place) => place.cell ?? place.shelf ?? place.zone) });
    };
    return { status: 200, body: view(planting), effect };
  }
  function harvest(world, request) {
    const body = object(request.body);
    const planting = structuredClone(plantingOf(world, request.params.planting_id));
    const at = iso(world.now);
    const apply = (target, crops) => {
      crops.harvests.push({ planting_id: target.id, id: crops.harvests.length + 1, ts: at, unit_count: body.count ?? null, weight_kg: body.weight_kg ?? null, notes: body.notes ?? null, operator: JASON.name });
      if (body.final) {
        for (const place of open(target)) place.until = at;
        Object.assign(target, { active: false, ended_at: at, end_reason: "harvested" });
      }
    };
    apply(planting, { harvests: [] });
    const effect = (next) => {
      apply(plantingOf(next, planting.id), cropsOf(next));
      log(next, { ...request, reason: body.reason }, "planting.harvest", planting.id, { ref: { table: "crops", id: planting.id }, count: body.count ?? null, final: Boolean(body.final) });
    };
    return { status: 200, body: view(planting), effect };
  }
  function remove(world, request) {
    const body = object(request.body);
    if (!["failed", "removed", "reconciled"].includes(body.end_reason)) throw invalid("end_reason is failed, removed or reconciled", "end_reason");
    const planting = structuredClone(plantingOf(world, request.params.planting_id));
    const at = iso(world.now);
    const apply = (target) => {
      for (const place of open(target)) place.until = at;
      Object.assign(target, { active: false, ended_at: at, end_reason: body.end_reason });
    };
    apply(planting);
    const effect = (next) => {
      apply(plantingOf(next, planting.id));
      log(next, { ...request, reason: body.reason }, "planting.end", planting.id, { ref: { table: "crops", id: planting.id }, end_reason: body.end_reason });
    };
    return { status: 200, body: view(planting), effect };
  }
  function patch(world, request) {
    const body = object(request.body);
    const fields = ["variety", "count", "stage", "notes", "expected_harvest"].filter((name) => name in body);
    if (!fields.length) throw invalid("Send at least one field to change", "body");
    const planting = structuredClone(plantingOf(world, request.params.planting_id, false));
    const changes = Object.fromEntries(fields.filter((name) => name !== "expected_harvest").map((name) => [name, body[name]]));
    Object.assign(planting, changes);
    const effect = (next) => {
      Object.assign(plantingOf(next, planting.id, false), changes);
      log(next, { ...request, reason: body.reason }, "planting.update", planting.id, { ref: { table: "crops", id: planting.id }, after: changes });
    };
    return { status: 200, body: view(planting), effect };
  }

  const get = (path, query = {}) => {
    const search = new URLSearchParams(query);
    return { method: "GET", url: `${path}${search.size ? `?${search}` : ""}` };
  };
  const send = (method, url, body) => ({ method, url, headers: {}, body });
  return {
    READS: {
      "API-CROP-01": locations,
      "API-CROP-02": list,
      "API-CROP-03": detail,
      "API-CROP-04": history,
      "API-CROP-05": () => ({ items: CATALOG.map(([slug, name, category]) => ({ slug, name, category })) }),
      "API-CROP-06": preview,
    },
    WRITES: { "API-CROP-07": plant, "API-CROP-08": move, "API-CROP-09": harvest, "API-CROP-10": remove, "API-CROP-11": patch },
    EXAMPLES: {
      "API-CROP-01": () => get("/v1/crops/locations"),
      "API-CROP-02": () => get("/v1/crops/plantings"),
      "API-CROP-07": () => send("POST", "/v1/crops/plantings", { crop: "lettuce", variety: "Romaine", positions: ["hydro 1-10"], count: 10, reason: "Re-plant" }),
      "API-CROP-03": () => get("/v1/crops/plantings/5"),
      "API-CROP-11": () => send("PATCH", "/v1/crops/plantings/5", { notes: "Several, hanging in the center; one spike" }),
      "API-CROP-08": () => send("POST", "/v1/crops/plantings/5/move", { to_positions: ["CENTER-HANG-1"] }),
      "API-CROP-09": () => send("POST", "/v1/crops/plantings/5/harvest", { count: 1, notes: "One spike cut" }),
      "API-CROP-10": () => send("POST", "/v1/crops/plantings/5/remove", { end_reason: "removed" }),
      "API-CROP-04": () => get("/v1/crops/positions/CENTER-HANG-1/history"),
      "API-CROP-05": () => get("/v1/crops/catalog"),
      "API-CROP-06": () => get("/v1/crops/resolve", { q: "hydro 1-10" }),
    },
  };
}

/** The crop operations in the order of the OpenAPI file (after API-IRR-13). */
export const CROP_ROUTES = [
  ["API-CROP-01", "GET", "/v1/crops/locations"], ["API-CROP-02", "GET", "/v1/crops/plantings"], ["API-CROP-07", "POST", "/v1/crops/plantings"],
  ["API-CROP-03", "GET", "/v1/crops/plantings/{planting_id}"], ["API-CROP-11", "PATCH", "/v1/crops/plantings/{planting_id}"],
  ["API-CROP-08", "POST", "/v1/crops/plantings/{planting_id}/move"], ["API-CROP-09", "POST", "/v1/crops/plantings/{planting_id}/harvest"],
  ["API-CROP-10", "POST", "/v1/crops/plantings/{planting_id}/remove"], ["API-CROP-04", "GET", "/v1/crops/positions/{label}/history"],
  ["API-CROP-05", "GET", "/v1/crops/catalog"], ["API-CROP-06", "GET", "/v1/crops/resolve"],
];
