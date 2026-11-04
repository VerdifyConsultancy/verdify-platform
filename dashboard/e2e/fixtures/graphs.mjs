// SPDX-License-Identifier: MIT
/**
 * WP2's named fixtures (Graphs, redesign §10.4). Each is a patch over the base `in-band` world, written with world.mjs's
 * exported helpers (`clock`, `lineOf`, `itemOf`, `settingOf`, `runLine`, `openAlert`, …) the way the fixtures in
 * world.mjs are, and it may apply a base fixture first (`import { FIXTURES as BASE } from "./world.mjs"`, then
 * `BASE["l01-running"](world)`). index.mjs registers this map
 * once, through `registerFixtures`; a name already registered (a base fixture's or another package's) fails the import,
 * so no package shadows another. Screen packages add fixtures here only, never in world.mjs or operations.mjs.
 *
 * @type {Readonly<Record<string, (world: any) => void>>}
 */
import { JASON, clock, iso, nextId, openAlert } from "./world.mjs";

export const FIXTURES = {
  // Graphs → Climate's story line and excursion shading (redesign §5.5): the band in force narrowed to 64.5–68.0 °F, so
  // the day's temperature wave (63–69 °F, coolest at 01:00, warmest at 13:00) runs too cold overnight, from about 21:00
  // to 05:00, and too warm again from about 09:50.
  "graphs-excursions": (world) => {
    world.band.temp_f = { ...world.band.temp_f, low: 64.5, high: 68 };
  },
  // The event rail (redesign §7.1), beside the base's Iris plan at 07:02, the forecast rule at 06:30 and L04's runs at
  // 06:58: Jason's change of the mister engage point at 08:42, an agent's of the fog ceiling at 09:00, and a band-drift
  // warning raised at 08:00 and resolved at 09:30.
  "graphs-events": (world) => {
    const at = clock(world.now);
    const id = nextId(world.activity);
    world.activity.push(
      {
        id, ts: iso(at("08:42")), actor: JASON, action: "setting.write", target: "mister_engage_kpa", detail: { before: 1.6, value: 1.55 }, reason: "Vandas drying",
        state: "confirmed", confirmed_at: iso(at("08:43")), experiment_id: null, request_id: "6a1e7c2d-3b4f-4e5a-8c9d-0e1f2a3b4c5d",
      },
      {
        id: id + 1, ts: iso(at("09:00")), actor: { source: "agent:steward", name: "steward" }, action: "setting.write", target: "fog_rh_ceiling_pct",
        detail: { before: 85, value: 82 }, reason: "Night humidity", state: "confirmed", confirmed_at: iso(at("09:01")), experiment_id: null, request_id: null,
      },
    );
    openAlert(world, {
      id: 501, alert_type: "house_band_drift", category: "climate", severity: "warning", message: "VPD above its band for 30 minutes", first_seen_at: iso(at("08:00")),
      last_seen_at: iso(at("09:20")), status: "resolved", resolved_at: iso(at("09:30")), resolved_by: "system", resolution: "back inside the band",
    });
  },
};
