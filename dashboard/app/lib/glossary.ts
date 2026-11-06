// SPDX-License-Identifier: MIT
/**
 * The one glossary (#1339): every technical term the dashboard shows, with a one- or two-sentence definition for
 * someone who knows plants rather than the controller. Now's `Term` opens an entry in place (a tap, never hover-only),
 * and Control → Settings lists the whole table at `#glossary`, so docs and MCP can cite `/settings#glossary`. Water,
 * Graphs and Control share it: a page that names a term defines it from here, never with its own copy.
 */

export type GlossaryEntry = {
  /** The words as a page shows them. */
  readonly term: string;
  readonly definition: string;
};

export const GLOSSARY = {
  vpd: {
    term: "VPD",
    definition: "Vapour pressure deficit: how strongly the air pulls water out of the leaves, in kPa. Low is damp and still; high is dry and makes plants close up.",
  },
  band: {
    term: "Band",
    definition: "The range of temperature, VPD and humidity the crops want right now. It follows the time of day, from the crop settings, and the controller works to keep the house inside it.",
  },
  in_band_today: {
    term: "In band today",
    definition: "The share of today's minutes, since local midnight, that the reading spent inside the band the controller was serving.",
  },
  mist_fog_limit: {
    term: "Daily mist & fog limit",
    definition: "The primary daily water limit: gallons through every mister and the fog today, against the controller's daily safety limit (360 gal). At the limit all misting stops except emergency fog. Normal misting first meets the lower daily budget (120 gal), which a VPD emergency may pass. Total water, drip irrigation included, is secondary.",
  },
  bursts: {
    term: "Bursts",
    definition: "Short manual runs that end on their own. Fans runs both fans with the vent open; Vent bypass runs both fans with the vent closed, stirring the house air; Humidify runs the fog. The controller ignores them during emergency cooling or heating and a sensor fault.",
  },
  occupancy_hold: {
    term: "Occupancy hold",
    definition: "While the cameras see someone inside, the misters wait so nobody is sprayed. The main light also comes on for anyone inside, at any hour.",
  },
  iris: {
    term: "Iris",
    definition: "The AI planner. At sunrise, midday, sunset, midnight and when the forecast changes, it writes a plan of settings for the hours ahead.",
  },
  iris_triggers: {
    term: "Iris plans",
    definition: "When Iris runs: Sunrise plan, Midday check, Transition (between parts of the day), Sunset plan, Midnight plan, and Forecast changed when the weather departs from the forecast. ✓ means it did its job.",
  },
  plan_changes: {
    term: "Setting changes",
    definition: "When a setting in the plan changes value in the hours ahead, from what the controller holds now to the new value. AI marks a change Iris planned.",
  },
  ai_mark: {
    term: "AI",
    definition: "Iris, the AI planner, chose this value. A value set by hand stays until it is released, and Iris leaves it alone.",
  },
  holding: {
    term: "In band, holding",
    definition: "The controller's resting mode: the house is inside the band, so nothing needs to run.",
  },
  house_band: {
    term: "House band",
    definition: "The band for the house as a whole. A zone without its own band, such as north, is read against it.",
  },
  peer_probes: {
    term: "From peer probes",
    definition: "The zone's own probe is not reporting, so its value is the average of the neighbouring zones' probes.",
  },
  derived: {
    term: "DERIVED",
    definition: "Under All readings: the band in force is computed from the crop band settings rather than measured. The mode code is the controller's own name for its mode.",
  },
  main_light: {
    term: "Main light",
    definition: "The overhead light on Lutron. The controller switches it on when daylight is low inside its hours, and for anyone inside at any hour.",
  },
  grow_light: {
    term: "Grow light",
    definition: "The plant light on Lutron. The controller switches it on when daylight is low inside its photoperiod, until the crops have had their light for the day.",
  },
  light_window: {
    term: "May switch on",
    definition: "The hours in which the controller may turn a light on. Whether it does depends on the daylight at the time, so this is a window, not a schedule.",
  },
  light_timer: {
    term: "Light timer",
    definition: "Turn on or Turn off holds for 15 minutes, then the light goes back to what the controller wants.",
  },
} as const satisfies Record<string, GlossaryEntry>;

export type GlossaryId = keyof typeof GLOSSARY;

/** Where the whole table is listed, for a Term's "All terms" link. */
export const GLOSSARY_PATH = "/settings#glossary";

export function glossaryEntry(id: GlossaryId): GlossaryEntry {
  return GLOSSARY[id];
}
