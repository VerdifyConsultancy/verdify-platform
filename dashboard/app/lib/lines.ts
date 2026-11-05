// SPDX-License-Identifier: MIT
import type { LineKind } from "./vocab";

/**
 * The line register L01–L11 (irrigation §3): each line's relay key in `equipment_state`, its zone for minutes by zone,
 * its chemistry and kind. Zones for minutes by zone: wall (L01 and L02, one valve for the south and west wall beds),
 * center (L04 to L06), south (L07 and L08) and west (L09 and L10); fog (L11) is reported on its own, as
 * humidification. L03 is the fertilizer supply valve: its zone is `supply`, and it never counts in a zone's minutes.
 * `name` is the line's display name (#1341), the same words the API serves (`LINE_NAMES` in verdify_services/labels.py):
 * every word a person reads names a line by it, never by its relay code; the code stays the machine key (ids, URLs, data
 * attributes, CSV id columns).
 */
export type MinutesZone = "wall" | "center" | "south" | "west" | "fog";
export type LineZone = MinutesZone | "supply";
export type Line = { id: string; name: string; equipment: string; zone: LineZone; chemistry: "clean" | "fert"; kind: LineKind };

export const LINES: readonly Line[] = [
  { id: "L01", name: "Wall drip", equipment: "drip_wall", zone: "wall", chemistry: "clean", kind: "drip" },
  { id: "L02", name: "Wall drip (fertilized)", equipment: "drip_wall_fert", zone: "wall", chemistry: "fert", kind: "fert_drip" },
  { id: "L03", name: "Fertilizer valve", equipment: "fert_master_valve", zone: "supply", chemistry: "fert", kind: "master" },
  { id: "L04", name: "Center mister", equipment: "mister_center", zone: "center", chemistry: "clean", kind: "mister" },
  { id: "L05", name: "Center drip", equipment: "drip_center", zone: "center", chemistry: "clean", kind: "drip" },
  { id: "L06", name: "Center drip (fertilized)", equipment: "drip_center_fert", zone: "center", chemistry: "fert", kind: "fert_drip" },
  { id: "L07", name: "South mister", equipment: "mister_south", zone: "south", chemistry: "clean", kind: "mister" },
  { id: "L08", name: "South mister (fertilized)", equipment: "mister_south_fert", zone: "south", chemistry: "fert", kind: "fert_mister" },
  { id: "L09", name: "West mister", equipment: "mister_west", zone: "west", chemistry: "clean", kind: "mister" },
  { id: "L10", name: "West mister (fertilized)", equipment: "mister_west_fert", zone: "west", chemistry: "fert", kind: "fert_mister" },
  { id: "L11", name: "Fog", equipment: "fog", zone: "fog", chemistry: "clean", kind: "fog" },
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

/** A relay code where a person would read it: L01–L11 as a whole word. */
export const LINE_CODE = /\bL(?:0[1-9]|1[01])\b/;
const LINE_CODES = /\b(L(?:0[1-9]|1[01]))(?:\s*[-\u2013]\s*(L(?:0[1-9]|1[01])))?\b/g;

/**
 * A line's display name (#1341): "Wall drip", "Wall drip (fertilized)", "Center mister", "Fog". Takes a line id or a line
 * from the API; an id the register does not know reads as the API's `name`, else as given. Never prefixes the code.
 */
export function lineDisplayName(line: string | null | undefined | { readonly line_id: string; readonly name?: string | null }): string {
  if (line === null || line === undefined) return "";
  const id = typeof line === "string" ? line : line.line_id;
  return BY_ID.get(id)?.name ?? (typeof line === "string" ? line : line.name || id);
}

/** The line a person names ("wall drip", any case) or keys ("L01"); undefined for anything else. */
export function lineByName(text: string): Line | undefined {
  const wanted = text.trim().toLowerCase();
  return BY_ID.get(text.trim()) ?? LINES.find((line) => line.name.toLowerCase() === wanted);
}

/** Words from the API or the controller with every relay code read as its line's name: "L01 is disabled" → "Wall drip is disabled"; "L04-L10" → "Center mister through West mister (fertilized)". */
export function nameLineCodes(text: string): string;
export function nameLineCodes(text: string | null | undefined): string | null | undefined;
export function nameLineCodes(text: string | null | undefined): string | null | undefined {
  if (!text) return text;
  return text.replace(LINE_CODES, (_match, from: string, to?: string) => to ? `${lineDisplayName(from)} through ${lineDisplayName(to)}` : lineDisplayName(from));
}
