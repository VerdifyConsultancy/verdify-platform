// SPDX-License-Identifier: MIT
import type { LineKind } from "./vocab";

/**
 * The line register L01–L11 (irrigation §3): each line's relay key in `equipment_state`, its zone for minutes by zone,
 * its chemistry and kind. Zones for minutes by zone: wall (L01 and L02, one valve for the south and west wall beds),
 * center (L04 to L06), south (L07 and L08) and west (L09 and L10); fog (L11) is reported on its own, as
 * humidification. L03 is the fertilizer supply valve: its zone is `supply`, and it never counts in a zone's minutes.
 * Line names come from the API (API-IRR-01 `name`), never from here.
 */
export type MinutesZone = "wall" | "center" | "south" | "west" | "fog";
export type LineZone = MinutesZone | "supply";
export type Line = { id: string; equipment: string; zone: LineZone; chemistry: "clean" | "fert"; kind: LineKind };

export const LINES: readonly Line[] = [
  { id: "L01", equipment: "drip_wall", zone: "wall", chemistry: "clean", kind: "drip" },
  { id: "L02", equipment: "drip_wall_fert", zone: "wall", chemistry: "fert", kind: "fert_drip" },
  { id: "L03", equipment: "fert_master_valve", zone: "supply", chemistry: "fert", kind: "master" },
  { id: "L04", equipment: "mister_center", zone: "center", chemistry: "clean", kind: "mister" },
  { id: "L05", equipment: "drip_center", zone: "center", chemistry: "clean", kind: "drip" },
  { id: "L06", equipment: "drip_center_fert", zone: "center", chemistry: "fert", kind: "fert_drip" },
  { id: "L07", equipment: "mister_south", zone: "south", chemistry: "clean", kind: "mister" },
  { id: "L08", equipment: "mister_south_fert", zone: "south", chemistry: "fert", kind: "fert_mister" },
  { id: "L09", equipment: "mister_west", zone: "west", chemistry: "clean", kind: "mister" },
  { id: "L10", equipment: "mister_west_fert", zone: "west", chemistry: "fert", kind: "fert_mister" },
  { id: "L11", equipment: "fog", zone: "fog", chemistry: "clean", kind: "fog" },
];

/** The rows of minutes by zone, in order: the four watering zones, then fog as humidification. */
export const MINUTES_ZONES: readonly MinutesZone[] = ["wall", "center", "south", "west", "fog"];
export const ZONE_WORDS: Readonly<Record<MinutesZone, string>> = { wall: "Wall", center: "Center", south: "South", west: "West", fog: "Fog" };

const BY_ID = new Map(LINES.map((line) => [line.id, line]));
const BY_EQUIPMENT = new Map(LINES.map((line) => [line.equipment, line]));

export function lineById(id: string): Line | undefined {
  return BY_ID.get(id);
}
export function lineByEquipment(equipment: string): Line | undefined {
  return BY_EQUIPMENT.get(equipment);
}
/** The minutes-by-zone row a line counts in; null for L03 (supply) and anything that is not a line. */
export function minutesZone(lineId: string): MinutesZone | null {
  const zone = BY_ID.get(lineId)?.zone;
  return zone === undefined || zone === "supply" ? null : zone;
}
