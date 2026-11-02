// SPDX-License-Identifier: MIT
import { readFileSync } from "node:fs";
import { expect, test } from "vitest";
import { LINES, MINUTES_ZONES, ZONE_WORDS, lineByEquipment, lineById, minutesZone } from "./lines";

const repo = new URL("../../../", import.meta.url);
const irrigation = readFileSync(new URL("docs/design/dashboard/irrigation.md", repo), "utf8");

test("the register is irrigation §3: L01–L11 in order, each relay key and zone as the design table has them", () => {
  expect(LINES.map((line) => line.id)).toEqual(["L01", "L02", "L03", "L04", "L05", "L06", "L07", "L08", "L09", "L10", "L11"]);
  // | **L04** Mister • Center | `center_mister`, pcf_out_1.5 (`mister_center`) | center (hanging Vandas) | …
  const rows = [...irrigation.matchAll(/^\| \*\*(L\d\d)\*\* [^|]+\| `[a-z_]+`, pcf_out_[\d.]+ \(`([a-z_]+)`\) \| ([a-z]+)/gm)];
  expect(rows.map(([, id, equipment, zone]) => [id, equipment, zone === "house" ? "fog" : zone])).toEqual(LINES.map((line) => [line.id, line.equipment, line.zone]));
});

test("minutes by zone: wall L01–L02, center L04–L06, south L07–L08, west L09–L10, fog L11 apart; L03 is supply", () => {
  const zones = Object.fromEntries(MINUTES_ZONES.map((zone) => [zone, LINES.filter((line) => minutesZone(line.id) === zone).map((line) => line.id)]));
  expect(zones).toEqual({ wall: ["L01", "L02"], center: ["L04", "L05", "L06"], south: ["L07", "L08"], west: ["L09", "L10"], fog: ["L11"] });
  expect(minutesZone("L03")).toBeNull();
  expect(minutesZone("L12")).toBeNull();
  expect(MINUTES_ZONES.map((zone) => ZONE_WORDS[zone])).toEqual(["Wall", "Center", "South", "West", "Fog"]);
  expect(LINES.filter((line) => line.chemistry === "fert").map((line) => line.id)).toEqual(["L02", "L03", "L06", "L08", "L10"]);
  expect(lineByEquipment("mister_center")?.id).toBe("L04");
  expect(lineById("L11")?.kind).toBe("fog");
  expect(lineByEquipment("grow_light_main")).toBeUndefined();
});
