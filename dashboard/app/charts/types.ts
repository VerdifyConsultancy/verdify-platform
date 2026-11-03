// SPDX-License-Identifier: MIT
/**
 * The OpenAPI schemas the charts read (docs/design/dashboard/openapi/dashboard-api.v1.yaml), as #1122's client parses
 * them: re-exports of the generated types through `Lenient` (operator-api.ts), so a screen hands a parsed response to a
 * chart unchanged and typecheck proves the match. A code newer than this client stays a string, which the charts show
 * raw; every field is read-only. Only `XRange` and `SettingSteps` are the charts' own.
 */
import type { components } from "../generated/operator-api";
import type { BandTimeline, Lenient, MinutesReport, Series, SettingHistory, StateLanes } from "../lib/operator-api";

export type {
  Actor, BandTimeline, DesiredSetting, MetricDef, MinutesReport, Plan, PlanLane, PlannerTrigger, PlanWaypoint, ReadbackState, Reading, Series, SeriesColumns,
  SettingHistory, StateLanes, StateReason,
} from "../lib/operator-api";

type Schemas = components["schemas"];
/** `NullableNumbers`: a missing bucket is null, never 0. */
export type NullableNumbers = Lenient<Schemas["NullableNumbers"]>;
/** `TimedValue`: a readback change point. */
export type TimedValue = Lenient<Schemas["TimedValue"]>;
/** `IrrigationCause`: why a wet line ran (irrigation §6.2); a newer cause stays a string. */
export type IrrigationCause = Lenient<Schemas["IrrigationCause"]>;
/** One `data_gaps` row (a telemetry outage the ingestor recorded) that overlaps the window. */
export type SeriesGap = NonNullable<Series["gaps"]>[number];
/** `BandColumns`: temperature and VPD only, so RH has no ribbon. */
export type BandColumns = BandTimeline["band"];
/** One `StateLanes` lane: an `equipment_state` key, its label from the API's `EQUIPMENT_LABELS`, and its intervals. */
export type StateLane = StateLanes["lanes"][number];
/** One `StateLanes` interval. `end` is clamped to the window or now; a wet line's `cause` is null until the run ledger fills it. */
export type StateInterval = StateLane["intervals"][number];
/** One `MinutesReport` day: `no_data` has no rows (a gap, never 0); today and outage days are `partial`. */
export type MinutesPeriod = MinutesReport["periods"][number];
/** One `MinutesReport` row, always split by cause and chemistry. `group` is a zone, a line id or a cause. */
export type MinutesRow = MinutesReport["rows"][number];

/** A chart's x range, UTC epoch seconds. */
export type XRange = { from: number; to: number };

/** One setting drawn as steps on a panel: its API-12 history and the words to call it. */
export type SettingSteps = {
  history: SettingHistory;
  label?: string;
  /**
   * Every desired row draws as a request labelled with who asked, and a release ends the step before it (the setting
   * drawer's history). Otherwise only the rows still on their way draw (Graphs). Read with the first answer, as `format`.
   */
  allRequests?: boolean;
  /** A value in words; `formatValue` in the panel's unit when omitted. */
  format?: (value: number) => string;
};
