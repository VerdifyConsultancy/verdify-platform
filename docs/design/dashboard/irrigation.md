# Irrigation program v2

Every wet line, clean and fertilizer, gets schedules that people and agents edit and that the ESP32
runs on its own. The platform keeps the schedules in a few tables and compiles them into one small
record: at most 24 schedule slots, plus line enables, mister flags, caps and pauses. The ingestor,
still the only device writer, pushes that record whenever the controller's published hash differs.
The controller keeps it in NVS, so watering, VPD misting and the existing caps carry on when the
ingestor, the database or WiFi is down. Each mister has two independent flags, `vpd_member` and
`schedule_member`. VPD and scheduled pulses share one manifold and the existing caps. Watering minutes
by zone come from relay edges, with the cause and outcome from the controller's job state. Whole gallons
per run and per day come from the calibrated site meter and are informational. L01, the wall drip, goes
first: the platform runs it before the new firmware ships (§6.5). Home Assistant keeps only occupancy
data, Lutron switch control and, until a direct reader replaces it, the hydroponic probe read. There are
no commissioning states, observe modes, envelopes or review dates: a line goes live once a person ticks
Checked.

Status: Draft v2 (2026-11-02), with Jason's Round 2 and 2b answers and the Q-01 to Q-14 defaults applied

Related: [README](README.md) · [Product](product.md) · [Screens](screens.md) ·
[Architecture](architecture.md) · [Irrigation](irrigation.md) · [Design system](design-system.md) ·
[Execution plan](execution-plan.md) · [OpenAPI](openapi/dashboard-api.v1.yaml) ·
[ADR-0011](../../adr/0011-irrigation-scheduling-program.md) (amends ADR-0007)

Evidence: paths are repo-relative at origin/main `0950a3bdb`; live facts (L01's last run, the occupancy
source, the meter's resolution) come from read-only checks on 2026-11-01. The decisions are Jason's own
([decisions](decisions.md)); where they differ the newest wins: Q-01 to Q-14, Round 2b, Round 2, then
the D-IRP rows. `IRR-Gnn` ids are survey references. Stories are the US-WAT and US-AGT stories in
[product](product.md); work items are the VD issues in the execution plan.

## 1. Decisions

This table applies Jason's answers exactly, with Round 2, Round 2b and Q-01 to Q-14. Where he accepted
a decision as written, v2 keeps what the decision does and drops any procedure it carried.

| ID | v2 decision | Jason |
|---|---|---|
| D-IRP-01 | Hybrid. The controller stores the compiled schedule and runs it offline. The platform authors and compiles it, then pushes it through the single writer | Accepted |
| D-IRP-02 | Clean water fails open within the existing caps. Fertilizer fails closed only for reasons in its own state: the line is disabled, the program is unreadable, or a fertilizer run was interrupted (it never resumes) | Accepted (no review dates; D-IRP-20) |
| D-IRP-03 | SAFETY_COOL survival fog preempts clean drips. A drip holds climate wetting for at most 15 min, then yields one pulse. Drips and mist still never overlap | Accepted (load-test clause dropped) |
| D-IRP-04 | The SAF-4 ceiling and the soft budget never veto survival fog; its water still counts. Ships in the engine release. Counting all other fog toward the caps is later | Accepted (sizing check dropped) |
| D-IRP-05 | When both are due, VPD and scheduled pulses alternate at pulse boundaries. A VPD emergency outranks schedules and pauses clean drips. Each mister zone keeps a VPD reserve that schedules cannot use | Accepted (the 60 % clause folds into the reserve) |
| D-IRP-06 | Caps: SAF-4 360 gal/day; mister duty center 120, south 45, west 45 min/day; VPD reserves 60/15/15 min; drips wall 60, center 30 min/day; same-line spacing 30 min; scheduled-mist RH ceiling 90 %; climate yield 15 min | Accepted (settle and the 30-day revisit dropped) |
| D-IRP-07 | A line is enabled once a person has checked it physically and ticked Checked on the dashboard, which records who and when (a note is optional). Agents cannot record the check | Accepted, simplified by the charter |
| D-IRP-08 | Turning Schedule on for a mister with no check needs the same check (for a mister: the nozzles, coverage and spray area). Center VPD membership never expires | Accepted, simplified |
| D-IRP-09 | A scheduled pulse is skipped on a measured RH ≥ 90 % or a measured dew-margin breach. No vent gate. Schedules never bypass the soft budget. Solar-night windows are allowed | Accepted (owner acknowledgement and #410 evidence dropped) |
| D-IRP-10 | Mist and fog hold only while Frigate confirms occupancy, through the Home Assistant occupancy sensor, the only input (Round 2b A; §7). Unknown or stale occupancy means mist runs | Changed |
| D-IRP-11 | "Mist now" is a one-off scheduled window on a mister with `schedule_member` on, under the same rails. There is no manual mister origin | Superseded 2026-11-05 (#1343): any wired, enabled line runs once, a mister with Schedule on or off, under the same rails |
| D-IRP-12 | Seed a sensor-fault backup set (labelled `sensor-fault backup` since migration 303, #1345; `vanda-degraded` before): three center slots marked `degraded_only`. They run only while VPD sensing is degraded, in daylight, while the dew margin can still be computed, and never in SENSOR_FAULT | Accepted (observe mode dropped) |
| D-IRP-13 | A zone whose own probe is out uses its peer probes, then the house average | Changed |
| D-IRP-14 | Every physically connected line is schedulable, fertilizer included. Fertilizer schedules run even with sensors offline. The firmware hard-block on L06, L08 and L10 becomes a per-line enable | Changed |
| D-IRP-15 | One clean flush follows a cancelled fertilizer run, except when disabling the line cancelled it | Accepted |
| D-IRP-16 | No bench or in-place test step. An enabled fertilizer line is schedulable at once, and run-now is available | Changed |
| D-IRP-17 | No latch. `leak_detected` raises the leak alert and does nothing else, because the one site meter also counts the hose, sink and hydro. The engine release drops it from every force-off and from the irrigation cancel (§4.7, F14) | Accepted, then changed in Round 2 (Q-05) |
| D-IRP-18 | The meter is calibrated. Whole gallons per run and per day show from day one and are informational. No flow flag and no per-run rate: a run that moved no water reads "0 gal" (Round 2b B, Q-10) | Changed |
| D-IRP-19 | Mister watchdog limit = commanded pulse + 30 s (ROM ceiling 300 s). A VPD-member zone backs off 10 min, then 30 min, before latching | Accepted (kept by the charter) |
| D-IRP-20 | No validation timelines, review dates or calibration requirements | Changed |
| D-IRP-21 | All agents, Iris included, read and write irrigation through one set of MCP tools, within the bounds in the irrigation registry | Changed |
| D-IRP-22 | No caretaker water limits. The roles are operator and viewer | Changed |
| D-IRP-23 | No OTA plan rules. Firmware ships when ready, through `make firmware-deploy` | Changed |
| D-IRP-24 | Home Assistant keeps only occupancy data and Lutron switch control, plus the hydroponic probe read until a direct reader replaces it (Round 2). Every other touchpoint goes (§7). The RUN CLEAN buttons go in the engine release, which adds run-now on the dashboard and in MCP; until then the platform presses the wall button itself (§6.5) | Changed |
| D-IRP-25 | NTP only. No RTC and no STOP button | Changed |
| D-IRP-26 | Dropped for now. Nothing is refused while an experiment is armed; each change made meanwhile is logged with the experiment id | Accepted, then dropped in Round 2 |
| D-IRP-27 | The WiFi `reboot_timeout` stays at 300 s (`firmware/greenhouse.yaml:247`) | Accepted |

## 2. Current state

- **No schedule exists, and L01 has not run since 2026-05-29.** #450 retired the fixed-clock jobs, and
  their globals are zeroed at boot (`firmware/greenhouse.yaml:95-119`). The wall drip's only trigger is
  the Home Assistant button "Wall Drips - RUN CLEAN" (`btn_wall_clean`,
  `firmware/greenhouse/tunables.yaml:2410-2425`), which nobody uses and the ingestor cannot press
  (IRR-G02). Pressing it during a run queues a second full run.
- **Only the center mister serves VPD control.** The resolver sends every climate request to L04 and
  refuses every other origin for it (`firmware/lib/irrigation_policy.h:66-79`). South and west are
  hard-coded off (`firmware/greenhouse/controls.yaml:1046-1047`) and have been idle since
  2026-07-10. No mister can be scheduled.
- **No fertilizer runs.** The weekly wall feed (L02 with L03) needs nine evidence bits, a revision and
  valid-until epochs; it was never commissioned, and from 2026-05-30 to 2026-07-10 every feed was
  dropped without a sound. L06, L08 and L10 are switched off unconditionally
  (`firmware/greenhouse/hardware.yaml:443-446, 465-468, 552-555`) and raise fence alerts
  (`ingestor/tasks/alerts.py:461-530`). L05 is physically unconnected and forced off at boot
  (`firmware/greenhouse.yaml:157-164`).
- **Nothing records why water ran.** `v_water_relay_runs` (migration 194) already turns wet-relay edges
  in `equipment_state` into runs, `daily_summary` keeps runtime per line per day, and
  `water_meter_events` (migration 102, attributed to relays since 194) carries the gallons. The job
  state, queue and reject reasons exist only as firmware globals (IRR-G01), so no run has a cause.
- **The caps leak.** The SAF-4 ceiling (`mister_daily_volume_max_gal`, 360 gal) and the center duty cap
  (`mister_max_runtime_min`, 120 min) have no readback and reset on every reboot
  (`firmware/greenhouse/globals.yaml:1284-1292, 1269-1272`; the counter `mister_water_today` is at
  `:1218-1221`; IRR-G06). The registry marks `mister_max_runtime_min` retired, but
  `controls.yaml:1040-1042` still enforces it. Mister water accrues only in the climate branch
  (`controls.yaml:1600-1618`). Any irrigation job, and the SAF-4 ceiling, can veto SAFETY_COOL survival
  fog (`controls.yaml:858, 911, 958-973`). Drips have no cap (IRR-G10).
- **The writer can call services but has no irrigation route.** The ingestor already runs staged
  ESPHome service transactions (`ingestor/esp32_push.py:1307-1392`). There is no irrigation API
  route and no MCP tool or grant (MCP-G18). Iris reads retired schedule views
  (`scripts/gather-plan-context.sh:435-470`; IRR-G05).
- **The heap has room for services, not entities.** July's Task WDT came from tunable-sync churn across
  about 612 entities (#428, #430). On 2026-10-18 about 63 kB was free in steady state. A pushed record
  costs a few kB; per-field schedule entities would cost tens.
- **There is one site meter.** A DAE pulse meter on GPIO33 measures all greenhouse water, including
  the hose, sink and hydro (`firmware/greenhouse/sensors.yaml:152-183`), in whole gallons. Jason
  confirms it is calibrated. `leak_detected` means flow with every Verdify valve closed, after a 120 s
  grace and 60 s of flow (`firmware/greenhouse.yaml:589-644`), so hand-watering reads as a leak.
- **Home Assistant is wired in far beyond occupancy and Lutron**: 13 touchpoints, listed in §7.

## 3. Lines L01-L11

| Line | Relay (`equipment`) | Zone | Today | v2 target | To bring online |
|---|---|---|---|---|---|
| **L01** Drip • Wall | `wall_drips`, pcf_out_1.4 (`drip_wall`) | wall | Not run since 2026-05-29; only the unused HA RUN CLEAN button starts it | Schedules plus run-now. The first line live: run-now through the bridge before the engine (§6.5), the first schedule after it. Seeded enabled, with no check | Record the check (flush the stagnant line, look at the emitters); add the first schedule |
| **L02** Drip • Wall (Fert.) | `wall_drips_fertilized`, pcf_out_2.0 (`drip_wall_fert`) | wall | Weekly feed, never commissioned | Fertilizer schedules with prewet and flush on L01. The feed comes from the tank through the injector that L03 opens | Check the line and record it; enable; add a feed schedule |
| **L03** Valve • Fert. Master | `fertilizer_master_valve`, pcf_out_2.1 (`fert_master_valve`) | supply | Opens only in the FEED stage | Opens the injector line from the fertilizer tank with any fertilizer run; never scheduled on its own | Checked together with L02 |
| **L04** Mister • Center | `center_mister`, pcf_out_1.5 (`mister_center`) | center | VPD only, implicitly; can never be scheduled | `vpd_member` on, as today; optional schedules; the `sensor-fault backup` set | Check nozzles, coverage and the spray area; set `schedule_member`; add schedules |
| **L05** Drip • Center | `center_drips`, pcf_out_1.7 (`drip_center`) | center | Physically unconnected; forced off at boot | Reserved (Round 2b E): stays in the model and can hold schedules; the Water screen shows it reserved and disabled until a person enables it | None now |
| **L06** Drip • Center (Fert.) | `center_drips_fertilized`, pcf_out_1.6 (`drip_center_fert`) | center | Off unconditionally | Reserved, as L05. Per-line enable; prewet and flush on L05 | None now |
| **L07** Mister • South Wall | `south_wall_mister`, pcf_out_1.3 (`mister_south`) | south | Idle since 2026-07-10 | VPD member, schedule member, or both | Drain the stagnant line before the first mist; check the nozzles; set the flags. Probe repair #751 is not needed (peer fallback) |
| **L08** Mister • South Wall (Fert.) | `south_wall_mister_fertilized`, pcf_out_1.2 (`mister_south_fert`) | south | Off unconditionally; fence-alerted | Per-line enable; flush on L07 | Plumbed (Round 2b E); joins the fertilizer bring-up after L02: check; enable; add a feed schedule |
| **L09** Mister • West Wall | `west_wall_mister`, pcf_out_1.0 (`mister_west`) | west | Idle since 2026-07-10 | VPD member, schedule member, or both | As L07; the west probe (Modbus address 3) is live |
| **L10** Mister • West Wall (Fert.) | `west_wall_mister_fertilized`, pcf_out_1.1 (`mister_west_fert`) | west | Off unconditionally; fence-alerted | Per-line enable; flush on L09 | As L08 |
| **L11** Fog (AquaFog XE 2000) | `fog_rly`, pcf_out_2.6 (`fog`) | house | Climate only | Climate only, unchanged; caps and drips never veto SAFETY_COOL fog | Nothing |

Zones for minutes by zone: wall (L01 and L02, one valve for the south and west wall beds, never split),
center (L04 to L06), south (L07 and L08) and west (L09 and L10). Fog is reported on its own, as
humidification minutes.

## 4. Schedule model

### 4.1 Where the state lives

- **Irrigation tables.** Schedules, lines, run and stop requests and outcomes live in irrigation's own
  tables, added by one migration (M3 in [architecture](architecture.md) §9). The minutes and day-coverage
  views ship in their own migration (M4), which needs only data that exists today, so it can merge before
  M3 (§6.2).
- **Site values.** Three site values are registry rows, written like any other setting through Settings
  (API-10) and MCP `set_setting`, and stored in `desired_state`: `mister_daily_volume_max_gal` (SAF-4,
  360, 100-600), `sched_mist_rh_max_pct` (90, 75-98) and `climate_yield_min` (15, 5-30). They reach the
  device only inside the program record. They have no `esp_object_id`: the settings validator accepts
  them as program-bound rows ([architecture](architecture.md) §5.3), the dispatcher skips them, and a
  write to one also wakes `irrigation_sync`. The compiler reads them from `v_desired_state_current`,
  falling back to the registry default. A site row reads CONFIRMED once `irr_program_id` confirms a
  program compiled at or after its write (`irrigation_push.site_desired_at`); before the engine
  firmware it reads "applies with the irrigation firmware".
- **Who pushes.** Only the ingestor's irrigation step pushes this state to the device (§6.1).
- **Logging.** Every write goes through `verdify_services.irrigation` and adds one `activity_log` row
  (`irrigation.*`) in the same transaction (§8.3).

| Table | Holds |
|---|---|
| `irrigation_line` | The 11 lines (seeded): `line_id`, `name`, `zone`, `kind` (`drip`, `mister`, `fert_drip`, `fert_mister`, `master`, `fog`), `relay`, `equipment`, `water_system` (the migration-085 slug), `device_idx` 0-10, `clean_partner`, the fields of §4.2, `checked_by`, `checked_at`, `check_note`, and attribution (`updated_by`, `source`, `updated_at`, `reason`) |
| `irrigation_slot` | One row per schedule (§4.3); attribution as above. The API and UI call each row a schedule; the device and this table call it a slot |
| `irrigation_command` | `run`, `stop`, `clear_fault` and `override` requests: `command_id` (uuid), `kind`, `line_id` (null means all), `args` (jsonb), `status` (`pending`, `sent`, `failed`), `fail_reason`, attribution, timestamps. Idempotency lives on the write's `activity_log` row (§8.3) |
| `irrigation_push` | Each compiled program sent: `revision` (a platform sequence, bumped only when the hash changes), `sha256`, `bytes`, `site_desired_at` (the newest site-value write compiled in), `pushed_at`, `confirmed_at`, `reject_code` |
| `irrigation_ledger` | The engine's outcomes, one row per occurrence or run-now (§6.2) |
| `v_irrigation_minutes_daily` | Minutes, pulses and gallons per America/Denver day by zone, line, cause and chemistry (M4, §6.2) |
| `v_irrigation_day_coverage` | Each America/Denver day's coverage: `complete`, `partial` or `no_data` (M4, §6.2) |
| `fn_irrigation_minutes_daily(from, to)` | The minutes view's rows for a day range, with the scan bounded by it (M4, §6.2) |

### 4.2 Line fields (editable)

| Field | Values | Rule |
|---|---|---|
| `enabled` | bool | Off stops everything on the line, VPD misting included, and its occurrences end `skipped` ("line disabled"). Turning it on needs a check (D-IRP-07) |
| `checked` | Who and when, with an optional note of at most 500 characters | Recorded only by a person on the dashboard; MCP tools take no check arguments. An agent that enables a line with no check, or turns `schedule_member` on for one, gets `check_required` (not retryable); once a check exists, agents change the line freely. No line is seeded with a check: L01 and L04 are enabled without one; L01's bring-up records it (§3), and L04 needs it before Schedule goes on |
| `vpd_member` | bool; L04, L07, L09 | Set with the line update (API-IRR-08). The zone takes part in climate VPD misting |
| `schedule_member` | bool; L04, L07, L09 | Set with the line update. The zone accepts mist schedules and run-now; turning it on for a line with no check needs one (D-IRP-08). Fertilizer misters are schedule-only, so `enabled` is enough for them |
| `daily_cap_min` | 0-480 (0 = none) | Seeds: L01 60, L04 120, L05 30, L07 45, L09 45; fertilizer lines 0 |
| `vpd_reserve_min` | 0 to `daily_cap_min` | Seeds: L04 60, L07 15, L09 15 |
| `paused_until` | timestamp or null | Blocks scheduled and run-now starts until then; those occurrences end `skipped` ("paused"). VPD misting carries on; to stop it on a zone, turn `vpd_member` off. A pause is also how one run is skipped: there are no per-occurrence skips |
| `manual` | bool | Iris leaves a manual line alone (schedules carry their own flag). Dashboard edits default to true and agent edits to false (§8.3) |

### 4.3 Schedule fields (the irrigation registry, `verdify_schemas/irrigation.py`)

| Field | Values | Notes |
|---|---|---|
| `slot_id` | 1-65535, never reused | The schedule's id on the device |
| `line_id` | L01, L02, L04-L10 | Not L03 (it opens with fertilizer runs) and not L11 (fog is climate only) |
| `enabled` | bool | Only enabled schedules are compiled into the record |
| `days` | A set of weekdays (at least one) | |
| `start` | `clock` HH:MM local, or `sunrise`, `solar_noon` or `sunset` ± 0-240 min | The device computes solar times itself (`firmware/lib/greenhouse_solar.h`); the week view uses the same calculation in `verdify_schemas/solar.py`, which `ingestor/solar.py` re-exports, with each date's UTC offset at local noon. A start is the first moment the America/Denver clock reads it that day: a time in the spring-forward gap runs when the clock jumps past it, a repeated fall-back time runs the first time, and a solar start past midnight (sunset + 240 in June) runs early the next morning |
| `on_s` | Drips 10-7200; misters 10-90 | Valve-open seconds per run or pulse. Fertilizer lines: the feed seconds |
| `count`, `gap_s` | Misters: 1-60 pulses, 30-600 s apart. Drips: 1 and 0 | At run time a mister gap is never shorter than `mister_min_off_s` |
| `prewet_s`, `flush_s` | 0-1800 each; fertilizer lines only | Clean water on the partner line before and after the feed. Set `flush_s` to the line's measured fill time |
| `degraded_only` | bool; L04, L07, L09 | See D-IRP-12. It follows `vpd_member`, not `schedule_member` |
| `label`, `manual` | | `manual` as for lines (§4.2) |

**Validation on submit** (shared by the API and MCP; values snap to whole seconds and minutes):

| Check | Code |
|---|---|
| The schedule or line does not exist | `unknown_target` |
| A field is out of its bounds for the line's kind | `out_of_range` |
| Two schedules on one line start less than 30 min apart (D-IRP-06) | `too_close` |
| More than 24 schedules in total, counted under the write lock (§8.3) | `too_many_schedules` |

**Accepted, then settled at run time.** A schedule on a disabled line, or a mist schedule on a zone
whose `schedule_member` is off, is stored; its occurrences end `skipped` ("line disabled", "not a
schedule member") until that changes. Runs past the line's daily cap end `cap`. The week view shows each
of these before it happens, so schedules can be built ahead of a line's bring-up. Its "ends at the daily cap"
note counts, per line and local day, the planned minutes that line's cap counts (§4.5): its own runs and
pulses, the prewet and flush a feed puts on it as the clean partner, and, on a mister zone, the feed pulses of
its fertilizer mister. A mister's budget is its cap less its VPD reserve. The editor's draft preview counts the same
way in the browser (`dashboard/app/lib/schedule-preview.ts`, VD-17), from API-IRR-01 and API-IRR-02's `solar[]`, and
leaves out `degraded_only` occurrences, which run only while VPD sensing is degraded.

Examples (illustrative):

- L01 on Mon, Wed and Fri at clock 07:30: `on_s` 600, `count` 1.
- L04 dawn drench: every day at sunrise +15, `on_s` 60, `count` 8, `gap_s` 60.
- L02 feed: Sundays at sunrise +60, `prewet_s` 120, `on_s` 300, `flush_s` 240.
- Seeded `sensor-fault backup` (`vanda-degraded` until migration 303) (`degraded_only`, enabled): L04 at solar noon −120, 0 and +120 min, each
  with `on_s` 45, `count` 4, `gap_s` 60.

### 4.4 Mister flags

| `vpd_member` | `schedule_member` | Behaviour |
|---|---|---|
| on | off | VPD control only. This is L04 today and the seed |
| off | on | Mist schedules and run-now only |
| on | on | Both. They share one set of caps |
| off | off | No mist; the mister's row says "This mister will not mist automatically". L07 and L09 seed here |

- **More than one VPD member.** The existing rank arbiter picks the zone (`arbitrate_wet_zone`,
  `firmware/lib/greenhouse_solar.h:236-251`, center at rank 1). The legacy stress score halves center
  (`mister_center_penalty` 0.5) and would move water away from the center zone.
- **Iris's VPD cadence tunables are unchanged.** Iris keeps `mister_engage_kpa`, `mister_pulse_on_s`,
  `mister_pulse_gap_s`, `mister_min_off_s` and `mister_vpd_weight`.
- **VPD source per zone (D-IRP-13).** A zone uses its own probe when the reading is fresh and finite.
  Otherwise it uses the mean of the fresh peer probes (south, west, east), and failing that the house
  average. Center always uses the house average, because it has no probe. `irr_state` publishes the
  source each zone is using.
- **Membership needs the line on.** The firmware's effective membership is `vpd_member` and the line
  enabled (`irr_vpd_zone_allowed`), so the API refuses `vpd_member: true` on a line that is and stays
  disabled (`line_disabled`, #1344), and Water shows VPD control on a disabled mister as words, never a
  switch reading a false ON.
- **What an operator reads (#1344).** VPD control sits on the mister's own row (Center, South or West
  mister), never on a drip, a feed or a card head, with an info tip in these words: when the greenhouse
  needs humidity and the climate safety checks pass, the controller may send short mist pulses to this
  mister. Only one mister runs at a time: Center goes first, unless another member's own zone is drier
  than its target, or Center is capped or faulted. Pulses count toward the mister's daily cap and the
  daily mist & fog limit (SAF-4); misting waits while someone is inside, a vent is open or a drip runs.
  South and West read their own probe, then the peer probes, then the house average. The tip fills in
  the cap and the limit from API-IRR-01.

### 4.5 Shared caps (all existing or accepted in D-IRP-06)

| Cap | Value | Counts | Stops |
|---|---|---|---|
| SAF-4 `mister_daily_volume_max_gal` | 360 gal/day | Water through every mister relay (VPD, scheduled, fertilizer) plus climate fog, as counted today | All mist and non-survival fog. Survival fog is exempt but still counted |
| Soft budget `mister_water_budget_gal` | 300 gal (Iris tunable) | Same | Mist and climate fog. Only VPD pulses in a VPD emergency may bypass it; schedules never do |
| Zone duty (`daily_cap_min`, L04/L07/L09) | 120 / 45 / 45 min | Every pulse on the zone, whatever its origin | That zone |
| VPD reserve (`vpd_reserve_min`) | 60 / 15 / 15 min | n/a | Scheduled pulses once the minutes used reach the cap minus the reserve |
| Drip line cap (`daily_cap_min`) | L01 60, L05 30 min; fertilizer lines none | Every run, prewet and flush included | That line |
| Re-fire fence `mister_min_off_s` | 45 s (Iris tunable) | n/a | Any pulse on the same zone |

The cap counters and the per-line daily runtime counters reset at local midnight. They live in an RTC
no-init block, so a soft reset (heap guard, WiFi reboot loop) keeps them. The block carries a magic
number, a layout version, the local day and a CRC32. On power-on, or a bad magic, version or CRC, the
whole block is zeroed; a new local day zeroes only the daily counters. Counters are integers (seconds
and centi-gallons), so they are never NaN, and SAF-4 still trips after a power cut.

### 4.6 How VPD and scheduled misting share the mist manifold

- **One mister relay at a time** (pressure group `mister_manifold`).
- **Alternation.** At each pulse boundary, a pending VPD pulse and a pending scheduled pulse take turns.
- **VPD emergency.** In a VPD emergency (`climate_vpd_emergency`, `controls.yaml:859-871`), VPD pulses
  go first. Windows on other zones wait, and clean drips pause.
- **Same zone.** When VPD and a window want the same zone, one pulse at the window's `on_s` serves
  both. It is counted once.
- **What a scheduled pulse needs.** It needs no climate demand.
- **What skips a scheduled pulse:** a measured RH ≥ `sched_mist_rh_max_pct`; a measured dew-margin
  breach; the soft budget, the SAF-4 ceiling, or the zone cap minus its reserve; confirmed occupancy; L03
  open, for clean mist. A missing sensor reading does not block (D-IRP-02).
- **SENSOR_FAULT.** The engine itself refuses scheduled, run-now, fertilizer and `degraded_only` mist in
  SENSOR_FAULT, because the existing relay lock covers only heat, fans, fog and vent. Invariant #26
  (mister relays off in SENSOR_FAULT) keeps holding.
- **End of a window.** A window ends when its pulses are done, or 60 min after its last planned
  pulse. Pulses not delivered by then make the outcome `short`.

### 4.7 Run rules

- **Drips.** One drip relay runs at a time (`drip_manifold`), first come first served by planned
  start, and drips never overlap mist or fog, as today. After 15 continuous drip minutes with climate
  demand waiting, the drip pauses for one VPD pulse (or up to 120 s of fog) and then resumes. SAFETY_COOL
  fog and a VPD emergency pause a clean drip at once; it resumes its remainder afterwards.
- **Fertilizer runs.** Prewet on the clean partner, then L03 with the fertilizer relay for `on_s` ×
  `count`, then the flush on the clean partner. A fertilizer run is never paused and never resumed: a
  stop, SAFETY_COOL or a reboot ends it, and the flush follows; disabling the line ends it with no flush
  (D-IRP-15). On the fertilizer misters L08 and L10, confirmed occupancy skips each feed pulse, as it
  skips a scheduled mist pulse (D-IRP-10); pulses not delivered by the window's end make the feed
  `short`, and its clean flush follows (D-IRP-15). SENSOR_FAULT refuses fertilizer mist like any mist,
  and invariant #26 covers L08 and L10. No climate or soil sensor gates a drip, clean or fertilizer.
  SAF-5 stays: fog and clean mist never run while L03 is open.
- **Leak (D-IRP-17, Round 2 #3, Q-05).** Hose, sink or hydro use with every valve closed reads as a leak
  (one site meter). `leak_detected` raises the existing leak alert, titled "Water flowing with every
  Verdify valve closed" (a leak, or the hose, sink or hydro in use), and does nothing else. The engine
  release drops it from the fog, mister and drip force-offs (survival fog included) and from the
  irrigation cancel (F14): closing Verdify valves cannot stop water that bypasses them. Until that
  release, today's firmware keeps its interlock: while the signal is up it holds fog, the misters and new
  drip starts off and cancels a running drip job. Nothing latches, and nothing needs clearing afterwards.
- **Run-now** starts at once or is refused, and nothing is queued: `line_busy` if that line is already
  running; `manifold_busy` if another valve holds the manifold (the detail names the line and until
  when); `cap_reached` beyond the line's remaining daily minutes; `line_disabled`; `line_paused`;
  `line_faulted` while a mister zone is latched (§5 F10). On a mister, run-now is a one-off window under
  the scheduled rails (the zone's duty cap less its VPD reserve, SAF-4, sensor fault), Schedule on or off
  (#1343, superseding D-IRP-11); until the controller runs the firmware with #1343 the API still refuses
  a mister with Schedule off (`not_schedule_member`), read from its `fw_version` stamp. The API refuses what it can see in the
  stored lines and the newest `irr_state` (422). The device may still refuse, for example mist in
  SENSOR_FAULT; every refusal but `line_busy` (and the device-only `bad_args` and `storage`) then ends
  the request `skipped` in `irr_events` (slot 0) with its reason, which the line shows in words (§4.8).
- **Stop.** Stop closes what is open now on one line or on every line, and it is never heap-deferred.
  It disables nothing: VPD misting, survival fog and the next scheduled run can still start on their
  own, and the Stop result says so. To keep a line off, disable it. Verdify has no main shutoff.
- **Time.** The device keeps time by NTP (`firmware/greenhouse.yaml:364-369`, America/Denver). With no
  valid time it starts nothing on schedule, and a running drip finishes on its own timer. Run-now and
  stop still work.
- **Exactly once.** Each occurrence, keyed by `(slot_id, local day)`, is claimed before its relay
  opens: the claim is saved and `global_preferences->sync()` runs first (the save-then-sync pattern of
  `irr_commit_claims()` in `firmware/lib/irrigation_program.h`), at most 24 times a day. A clean run cut by a soft reset resumes
  its remainder from the RTC block; after a power cut it is not repeated. A start may be up to 60 min
  late; after that the occurrence is `missed`.
- **Outcomes.** Every occurrence ends in exactly one outcome: `ok`, `short`, `stopped`, `preempted`,
  `skipped`, `missed` or `cap`, with a reason. `irr_events` publishes it with a sequence number and
  `irrigation_ledger` keeps it (§6.2). One the platform never received shows as a gap, never as
  `missed`.
- **Offline.** With the ingestor, the database or WiFi down, the device runs its last program, VPD
  misting and every cap, and any pauses already pushed. Agent and weather adjustments stop until the
  platform returns.
- **ROM default.** If the program is missing or unreadable, the device does exactly what it does
  today: L04 VPD only, L01 enabled for run-now, no schedules, fertilizer lines off.

### 4.8 What the engine core settles (VD-44)

`firmware/lib/irrigation_program.h` implements §4.5 to §4.7. These are the points the design left open,
as the core settles them:

- **One engine run at a time.** Drips never overlap mist or fog, so the engine runs one occurrence or
  run-now at a time across both manifolds, first come first served by planned start. Climate VPD pulses
  are the climate path's: they ask for the mist manifold through `irr_vpd_request()` (grant, served,
  wait or refuse) and hand it back with `irr_vpd_release()`. A run-now while another run holds the
  water is `manifold_busy`.
- **Claims cover every outcome.** An occurrence is claimed, saved and then synced, before its first
  relay opens and also before an outcome that opened nothing (`skipped`, `missed`, `cap`) is published,
  so a reboot never repeats a run or an outcome; still at most 24 a day. A claim that cannot be saved is
  retried each tick and dropped unpublished 10 min past its deadline.
- **Fertilizer runs.** The outcome is published when the feed ends. The flush then runs as the run's
  tail, journaled in the claims record, so a reset repeats the flush and never the outcome. An outcome
  goes out only once its journal step (the flush, or the cleared journal) has saved and synced, as a
  claim does, so a reset never ends a run twice. A flush journal that does not save is retried each
  tick for up to 60 min, then the run ends with no flush; a cleared journal that does not save is
  retried for 10 min, then the outcome is dropped unpublished and a later reset ends the run once, from
  its journal. A fertilizer run does not start during SAFETY_COOL or a VPD emergency. L03 opens with
  every feed; its own enable bit is not read (its fence rule is its own, §6.3). SAFETY_COOL ending a
  prewet or feed reads `preempted`, with no reason.
- **Prewet and flush.** They open the clean partner, and the engine never opens a disabled line (§4.2,
  §6.3): a run with a prewet or flush whose partner is disabled ends `skipped` (`dis`) when due
  (run-now: `line_disabled`), and disabling the fertilizer line or that partner mid-run ends it
  `stopped` (`dis`) with no flush. The soft budget, the zone duty cap and the drip caps count them but
  do not stop them, because they belong to a run that already started. On a mister they are mist, so
  SENSOR_FAULT, a latch or back-off, confirmed occupancy (D-IRP-10) and the SAF-4 ceiling hold them: an
  open mist prewet so held ends the run (`cap` with `saf`, otherwise `short` with the reason) and the
  flush follows; a mist flush closes and waits, and also yields to a VPD emergency. A drip flush pauses
  for SAFETY_COOL and a VPD emergency. A prewet that cannot open within 60 min of its run's start ends
  the run `short`, and a flush that cannot run for 60 min is dropped.
- **SAF-5 on mister feeds.** L03 opens only with each feed pulse of L08 and L10. A feed pulse waits
  until fog reads OFF, and the engine's `l03_hold` asks the climate path to stop fog first.
- **Mist windows.** Pulses are planned from the window's first pulse, and a blocked pulse waits rather
  than being dropped. A window whose first pulse cannot open within 60 min of its planned start is
  `missed`; once started, it ends 60 min after its last planned pulse, `short` with the last blocking
  reason. SENSOR_FAULT, confirmed occupancy, a latch or a cap cuts a pulse at once; a cut pulse is
  delivered again later.
- **Settled at the start.** A due occurrence is settled in the order the agenda predicts it
  (`verdify_schemas/irrigation.py`): `dis`, then `nvm` or `nsm`, then `pau`, then the caps. One whose
  cap is already reached ends `cap` at once, with `cap`, `res`, `saf` or `bud`. Run-now is refused
  `cap_reached` when the requested valve time is more than is left under the line's cap (for mist, the
  zone's cap less its reserve), or when SAF-4 or the soft budget is reached.
- **Run-now refusals are outcomes.** Every refusal but `line_busy` (a repeated send) ends the request
  `skipped` in `irr_events`, slot 0, with its reason: `dis`, `pau`, `flt`, `nsm`, `sf`, `cap`, `res`,
  `saf`, `bud` or `man`. The device-only `bad_args` (outside the line kind's bounds) and `storage` (a
  fertilizer journal that did not save) end nothing. A fertilizer run-now during SAFETY_COOL is
  `manifold_busy`.
- **`degraded_only`.** Outside daylight such an occurrence ends `skipped` with no reason, and a
  fallback window still running at sunset ends there, `short` with no reason. One that never fires ends
  `skipped` with its last waiting reason: `vok` while VPD sensing stayed healthy or was out for under
  300 s, `sf` in SENSOR_FAULT.
- **Drip yield.** A clean drip, and the clean flush tail of a fertilizer drip run, yields as D-IRP-03
  says. A yield ends after one VPD pulse, after 120 s of fog, or after 60 s in which no climate relay
  turned on. A fertilizer prewet or feed never yields, because it is never paused: it holds climate mist
  and fog until it ends (SAFETY_COOL ends it).
- **Counting.** Runtime accrues at each engine tick from the relay states the caller samples, so a VPD
  pulse is counted to the tick. Every mister relay counts toward its zone (L08 south, L10 west). The
  engine reports the commanded length of each mister it opens (`mist_cmd_s`) for the D-IRP-19
  watchdog, since a prewet or flush on a mister can run up to 1800 s.

### 4.9 What the engine wiring settles (VD-45)

`firmware/greenhouse/irrigation_engine.yaml` and `firmware/lib/irrigation_engine_esphome.h` put the core
on the controller; the pure part of the wiring is in `irrigation_program.h` with the core. These are the
points the design left open, as the wiring settles them:

- **Start.** The first 5 s tick binds the relays, the three text sensors and `irr_grant_mask`, then starts
  the engine: the RTC block by the reset reason, the claims record, and the stored program (`src=nvs` when
  it decodes against its hash). In the seconds before that, every wet relay guard refuses, a VPD pulse
  gets no grant and the services log and ignore the call; the ingestor's 60 s pass pushes again.
- **Clock.** The tick takes the local offset from one ESPTime reading (its local hour, minute and second
  against its UTC epoch), so DST changeovers follow §4.3's rules by construction.
- **Grant order.** Every engine call is applied the same way: `irr_state` is published if it changed, the
  bits of the relays about to open go into `irr_grant_mask`, the relays the engine let go close (L03
  last), the engine's relays open (L03 first), and the bits of what closed clear. A fertilizer relay no
  longer opens L03 itself; the engine opens it with each feed.
- **Climate closes.** The climate path never closes a mister the engine's own run holds (a window, a feed,
  a mist prewet or flush): its every-tick and pulse-end turn-offs go through `irr_climate_close()`, which
  closes only what the engine does not hold and hands back the climate's own VPD grant. The fertilizer
  relays are the engine's alone.
- **VPD grants.** `irr_vpd_grant(idx, pulse_ms)` carries the pulse's commanded length, which is the `m=`
  token's planned end; the climate path works out the weighted S2 length before the pulse opens. A wait,
  a refusal or a pulse already served by a window on the zone opens nothing, and the climate path asks
  again one pulse later.
- **Zone fence.** An engine pulse that closes, a VPD release and a stop stamp the zone's off time when
  they happen. The next tick's relay sample stamps only the closes the engine did not see (a mist prewet
  or flush, a relay closed outside an engine call), so a window's pulses run `max(gap_s,
  mister_min_off_s)` apart and `irr_state` does not change between them.
- **Runs not yet open.** A run whose relay cannot open yet (a drip waiting for a VPD pulse or fog to end,
  a feed waiting for fog, a flush that opens at the next tick) is published as `w=<line>,man`, with the
  VPD pulse's end when one holds the manifold, never as a `d=` or `m=` token with no start time.
- **Stop.** A stop closes the wet relays in its scope inside the call (255: L01 to L10), the climate's VPD
  pulse included. Fog is climate only: a stop never touches it, and a stop naming L11 does nothing.
- **Program push.** A commit that decodes is saved to NVS and synced, then runs from the next tick. If the
  save or sync fails the old program keeps running with no reject token (the `j=` vocabulary has none for
  storage), and the ingestor's next pass pushes again. A commit with no begin does nothing.
- **Water and mirrors.** The flow accrual adds integer centi-gallons to the RTC block and carries the
  fraction; `mister_water_today` mirrors the block at once, and `runtime_mister_*_ms` and
  `runtime_*_drip_ms` at each tick. Since VD-46 (§4.10) the flow meter counts while any mister relay or
  fog is ON, whatever opened it.
- **Climate inputs.** Each second the climate loop writes the engine's inputs: SENSOR_FAULT and
  SAFETY_COOL from the mode, degraded sensing (the average probes out for 300 s straight, so a reboot
  inside a due window never counts, #1271), the VPD emergency, the
  measured RH and the dew margin, the soft budget, `mister_min_off_s`, confirmed occupancy, the VPD source
  per zone and the climate-yield demand (fog or VPD mist that only a drip holds back). None reads the
  leak. The RH and the dew margin come from one measured pair: the average probes, else the
  controller-case probe while it answers, never the loop's fabricated fallback values. With neither the
  RH reads missing (it never blocks) and the margin unknown. Degraded sensing means the averages have been
  out for 300 s, so the case probe is what lets a `degraded_only` window run (D-IRP-12). The climate loop's
  own VPD trust gate takes no grace: it only holds VPD wetting back, so it trips at once.

### 4.10 What the mist path settles (VD-46)

`firmware/greenhouse/controls.yaml` (sections 9 to 14) and the mist-path half of
`firmware/lib/irrigation_program.h` carry F6, F7, F9, F10 and the mist and fog part of F14. These are the
points the design left open, as the implementation settles them:

- **Which zone may take a VPD pulse.** `irr_vpd_zone_allowed()` is the one rule per zone: the climate's
  demand and its safety gate, an effective VPD member (the program's VPD-member bit and the line's enable
  bit), no back-off or latch on any line of the zone (a fault on L08 holds L07 too), the zone's duty cap
  (the clean line's `daily_cap_min` against every mister second on the zone, any origin) and SAF-4.
  Confirmed occupancy, the soft budget, a drip and the vent interlock stay in `mister_blocked`. Before
  the engine starts no zone is allowed (no grant could be given either).
- **Zone choice.** The 10 min fairness override goes first. With two or more zones allowed,
  `irr_rank_vpd_zone()` picks: the best owner rank (`zone_priority_*`, center 1) among the zones above
  their VPD band target, and when none is (the house-level stress asked for the pulse) the best-ranked
  allowed zone. With one zone allowed the legacy stress score runs as before; it can only pick that zone,
  and it keeps `mister_center_penalty` a live policy consumer. No control path reads
  `sw_arbiter_zone_enabled`; only its `cfg_arbiter_zone_enabled` readback does.
- **Scheduled pulses at the boundary.** The engine's manifold arbitration (§4.8, VD-44) already
  alternates VPD and window pulses, puts VPD first in an emergency and serves both with one window pulse
  on the same zone. The climate path now asks it for any zone's grant at each pulse boundary
  (`irr_vpd_grant(zone_line, pulse_ms)`); the vent kill closes only the climate's own VPD pulses, never
  an engine-held relay (D-IRP-09).
- **Water.** The flow meter counts into SAF-4 and the soft budget whenever any mister relay (L04,
  L07-L10) or fog reads ON, outside the `mister_blocked` branch. Runtime per zone is the engine's (every
  relay ON).
- **Survival fog (F9).** `fog_force_off_reason()` in `greenhouse_logic.h` is the one force-off rule for
  the fog gate, the relay and the `fog_block_reason` ladder. Every mode: SENSOR_FAULT (`sensor_fault`),
  confirmed occupancy (`occupancy`), L03 open or about to open (`fert_master`), a drip relay reading ON
  (`irrigation`) and an invalid clock (`time_invalid`). Outside SAFETY_COOL also the engine's drip
  (`irrigation`), the soft budget (`resource_budget`) and SAF-4 (`water_ceiling`). The drip-relay term
  keeps drips and fog apart for the up to 5 s before the engine's tick pauses a clean drip in
  SAFETY_COOL. `sensor_fault`, `fert_master` and `water_ceiling` are new ladder words
  (`verdify_schemas.climate_intent.FOG_BLOCK_REASONS`); `leak_detected` stays there for history.
- **The watchdog (F10).** `irr_watchdog_step()` runs in the climate loop each second. A mister's limit
  is its commanded length + 30 s: the VPD pulse's from its grant (the lapse of an unreleased grant
  coincides with it), an engine open's (`mist_cmd_s`) from its ON edge, else 300 s. An overrun is forced
  OFF and counted on the zone; the engine drops the open at once (`irr_apply_faults()`): a pulse counts as
  delivered and the next waits, a mist prewet or flush stops its clock. Whether a fault backs off or
  latches follows the faulted line: an effective VPD member backs off 10, then 30 min, then latches; any
  other mister, L08 and L10 included, latches at once. A wet relay ON without its grant bit is forced
  OFF too, with no fault counted.
- **Fault state.** Per zone in the RTC block (layout version 2): the count and the latch zero at local
  midnight, a back-off is a countdown that runs across midnight and survives a soft reset (a power cut
  clears all of it). irr_state's `f=` token names the zone's last faulted line with the back-off's end or
  the latch's local midnight; with no valid clock the end is filled in once there is one.
  `clear_fault` zeroes the zone's count and latch (255: every zone); a back-off still running ends at its
  own time.

## 5. Firmware changes

All of this is one release. The pure logic lives in headers under `firmware/lib/`, with native tests
in `firmware/test/test_irrigation_program.cpp` on the golden vectors.

| # | Change | Where | What |
|---|---|---|---|
| F1 | Engine | New `firmware/lib/irrigation_program.h` and `firmware/greenhouse/irrigation_engine.yaml` (5 s loop) | Decodes and validates the program; due test; claims; manifold arbitration; caps; fertilizer sequence; resume rules. Replaces the job machine (`controls.yaml:2307-2650`), the weekly-feed commissioning gate and its state machine (`irrigation_policy.h:125-513`; the save-then-sync journal pattern is reused for fertilizer runs), and the boot-time zeroing of the legacy schedule globals and wall-feed journal recovery (`firmware/greenhouse.yaml:95-156`). The globals themselves go with their `cfg_*` readbacks in F13 |
| F2 | Program services | `irrigation_engine.yaml` | `irr_program_begin(len, sha256_hex)`, `irr_program_chunk(offset, data_hex)` (at most 192 B), `irr_program_commit()`. The same staged shape as the policy engine (`firmware/greenhouse/policy_engine.yaml:34-146`). A failed commit keeps the old program and publishes its reject code in `irr_state`: `bad_length`, `bad_magic`, `bad_hash`, `bad_header`, `bad_line` or `bad_slot` (VD-36) |
| F3 | Published job state | 3 text sensors, published on change only, never with per-tick progress, in the grammar VD-36 freezes in `verdify_schemas/irrigation.py` | `irr_program_id`: `h=<first 16 hex of sha256> src=nvs\|rom`; under `src=rom` it reads `h=0000000000000000`, so every desired program differs and is pushed. `irr_state` (at most 255 chars): the active run on each manifold (line, cause, slot, start and planned end; a `degraded_only` run carries cause `fallback`, which is its fallback flag); the waiting or blocked reason for each due run; faulted mister zones with each back-off or latch end time; the VPD source per zone; the last push reject. Past 255 chars the `w=` (waiting or blocked) tokens drop first. It is published before a relay opens, so every ON edge has a cause. `irr_events`: the newest 8 outcomes, kept in the RTC block, each `<seq>,<device_idx>,<slot>,<local_day>,<outcome>[,<reason>]`, with a u16 sequence and slot 0 for run-now; a reboot ends a feed `short` with reason `rst`. Enables and flags are not echoed; the program hash proves them |
| F4 | Run-now, stop, clear | `irr_command(cmd, line, on_s, count, gap_s, prewet_s, flush_s)` | `run`, `stop` (one line, or 255 for all; never heap-deferred) and `clear_fault` (a mister latch). A run on a running line is refused (`line_busy`), so a repeated send never starts a second run. Pauses are program data, so they survive reboots |
| F5 | Per-line enables | Relay guards, `hardware.yaml:410-642` | Every wet relay guard checks one engine grant instead of the per-origin tokens. The unconditional `turn_off()` on L06, L08 and L10, and the per-tick fertilizer off in `controls.yaml:1081-1083`, become per-line enables. L03 opens only during a fertilizer run |
| F6 | Mister membership and schedules | `irrigation_policy.h:66-79`; `controls.yaml:1029-1053, 1282-1504, 1600-1618` | Delete center-only routing and reverse exclusivity. `south_wet_allowed` and `west_wet_allowed` come from the engine's effective VPD membership. The rank arbiter is on when there is more than one member. Scheduled pulses enter at the pulse boundary. Water and runtime accrue for every mister relay ON |
| F7 | Occupancy rule | `controls.yaml:856, 911, 1147` | Scheduled and fertilizer mist use the same `occupancy_moisture_block` as VPD mist and fog. It is true only while a detection pushed by the ingestor holds (§7); the firmware's 60 min expiry (`controls.yaml:106-129`) is a fail-safe. Drips ignore occupancy |
| F8 | Peer-probe fallback | New `zone_vpd_with_fallback()` in `firmware/lib/`, used by zone choice (`controls.yaml:1282-1320`) | Own probe, then peers, then house average |
| F9 | Survival fog | `controls.yaml:911, 958-973` | In SAFETY_COOL, drop `irrigation_water_conflict`, `mister_volume_hard_block` and `climate_water_budget_block` from the fog force-off. Every other term stays except the leak (F14): L03 open, no valid controller time, confirmed occupancy, the SENSOR_FAULT lock and the fog RH ceiling; a drip relay that still reads ON holds fog until the engine's next tick pauses it (§4.10) |
| F10 | Relay watchdog (D-IRP-19) | `controls.yaml:1074-1094` | A wet relay ON without a grant is forced off. A mister ON longer than its commanded pulse + 30 s (ROM ceiling 300 s) is forced off. A VPD-member zone backs off 10 min, then 30 min; a third fault in a day latches it until midnight or `clear_fault`. A mister zone that is not a VPD member (schedule-only, and the fertilizer misters L08 and L10) latches on its first fault |
| F11 | Caps from the program | `controls.yaml:876-883, 1040-1042`; `globals.yaml:1218-1221, 1269-1272, 1284-1292` | SAF-4 and per-line caps come from the program; L04's `daily_cap_min` replaces `mister_max_runtime_min`. The daily counters move to the RTC block (§4.5) |
| F12 | HA buttons out, `manual_override` in | `tunables.yaml:2410-2425`; `greenhouse.yaml:484-524, 736-784`; `hardware.yaml:815-878` | Below |
| F13 | Retired entities | `tunables.yaml`, `sensors.yaml`, `globals.yaml`, `greenhouse.yaml:157-164` | `sw_irrig_*` (6), `sw_wall_fertigation_commissioned`, `num_irrig_*` (4, with the `irrig_vpd_boost_pct` and `irrig_vpd_boost_threshold_hrs` rows), `num_wall_*` (8), `num_post_feed_hold_min`, their `cfg_*` readbacks (35, the legacy schedule readbacks included) with the legacy schedule globals those read (F1 leaves them), the SAF-4 number entity (`tunables.yaml:1754-1770`), `mister_max_runtime_min` and the boot enable overrides. Keys in `ANCHOR_SYNC_PARAMS` stay, and so does the program-bound `mister_daily_volume_max_gal` row (§4.1). Registry rows (`verdify_schemas/tunable_registry.py`) and routes in `ingestor/entity_map.py` retire in the same PR, because a half-retired knob once crashed the writer; the L01 bridge's rows and routes wait for a follow-up (§6.5). While the pre-engine firmware still runs after the platform sync, the ingestor logs no errors for them and the dispatcher pushes none of them |
| F14 | Leak alerts only (Q-05) | `controls.yaml:911, 914-921, 958-973, 1142-1150, 1916-1917, 2520`; `irrigation_policy.h:396-406` | Drop `leak_block` from the fog force-off (survival fog included), the mister block, both block reasons and the interlock log. The engine has no leak cancel: `cancel_all("leak")` and `leak_blocked` go with the job machine (F1). `bs_leak_detected` stays as the alert's input |

**F12 in detail.** Remove the 2 RUN CLEAN buttons and the 7 other HA buttons with their scripts. One
`manual_override(kind, minutes)` service replaces them, called by the ingestor from `override` command
rows: `fans`, `humidify` and `vent_bypass` set `manual_fans_until_ms`, `manual_fog_until_ms` or
`vent_bypass_until_ms` to now + minutes; `clear` zeroes all three; `restart` calls `App.safe_reboot()`.
It never touches occupancy, so a burst never lifts the occupancy hold on mist and fog. The physical
push buttons (PB • Fan Burst, PB • Vent Bypass, PB • Fog Burst) toggle the same latches. Delete in the
same PR: `occupancy_quiet_override` and `occupancy_override_until_ms` with their branches
(`controls.yaml:107-117`, `hardware.yaml:325-338`), `bs_occupancy_quiet_override`, the
`manual_fan_burst`, `manual_fog_burst` and `vent_bypass_timer` scripts and flags (`controls.yaml:719-721`
then reads only the latches), and `num_fan_burst`, `num_vent_bypass` and `num_fog_burst` with their
registry rows and routes. `bs_manual_fan`, `bs_vent_lock` and `bs_manual_fog` stay, reading the
latches, so `fan_burst_active`, `vent_bypass_active` and `fog_burst_active` confirm each burst.

As built (VD-47): the latch arithmetic is three pure, native-tested helpers in
`firmware/lib/greenhouse_logic.h` (`manual_override_deadline_ms()`, `manual_latch_active()` and
`manual_latch_toggle()`), so the push buttons clamp `manual_override_timeout_min` to the same 1-120
minutes as the service. F13 also deleted the globals #1160 left with no reader (the weekly-feed journal
`wall_feed_*`, the job machine's cycle and last-day counters, `irrig_vpd_stress_ms` and
`outdoor_rh_mode`). With no entity in the engine firmware, the L01 bridge's rows carried their own step,
label and unit, and the two `irrig_*_duration_min` rows dropped their firmware clamp (`min` and `max` kept
1-120); `RETIRING_TUNABLES` kept the bridge's eight pre-engine entities until the follow-up (§6.5)
retired the bridge's rows and routes and emptied the set.

**Tests and docs pinned to ADR-0007 or the leak interlock** change in the same release (F6 to F10,
F14): `firmware/test/test_greenhouse_logic.cpp:5263-5298`; invariants #2, #15 and #26 in
`firmware/test/invariants.h`; `tests/test_12_fidelity.py:2603-2627, 2929-2968, 3037, 3134`;
`verdify_schemas/tests/test_tunable_registry.py:431-447, 459-478`; and `docs/firmware-fsm-spec.md`,
`docs/CONTROL-ARCHITECTURE.md` and `docs/firmware-control-contract.md`.

**Program record.** A fixed 584 B, little-endian; the packer, the decoder's golden vectors (an empty
program, the seed program and a full one) and the byte layout live in `verdify_schemas/irrigation.py`.
The SHA-256 covers the whole record. The record carries no revision, so an unchanged program compiles
to the same hash. It goes in 4 chunks of at most 192 B. Only enabled schedules are compiled, ordered by
`slot_id`; if more than 24 are ever enabled, the compiler keeps the 24 lowest and reports the rest as
not on the controller. A stored schedule outside its line kind's bounds (only a row written around
validation can be) is left out and reported the same way, so one bad row never blocks the program.

| Bytes | Part | Content (offset: field) |
|---|---|---|
| 0-15 | Header | 0: magic `VIP1`, which also versions the format; 4: SAF-4 gal u16; 6: RH ceiling u8; 7: climate yield u8; 8: enabled mask u16 (bit = `device_idx`); 10: VPD member mask u8 and 11: schedule member mask u8 (bits L04, L07, L09); 12: slot count u8; 13-15: reserved 0 |
| 16-103 | Lines | 11 × 8 B by `device_idx`: +0 `daily_cap_min` u16; +2 `vpd_reserve_min` u16; +4 `paused_until` u32 (UTC epoch seconds, 0 = none) |
| 104-583 | Slots | 24 × 20 B, unused slots zero: +0 `slot_id` u16; +2 line u8; +3 weekday mask u8 (bit 0 = Monday); +4 start kind u8; +5 flags u8 (bit 0 `degraded_only`); +6 start i16 (minute of day, or the solar offset); +8 `on_s` u16; +10 `gap_s` u16; +12 `prewet_s` u16; +14 `flush_s` u16; +16 `count` u8; +17-19 reserved 0 |

**Heap.** The release adds 5 services, 3 text sensors and a 584 B program held in RAM and NVS, with
static 256 B buffers. It removes about 70 entities (F12, F13). Free heap goes up.

**Upload.** The OTA upload blocks the main loop, so no pulse timer, engine tick or watchdog runs until
the reboot or an abort. `ota.on_begin` in `firmware/greenhouse.yaml` closes every wet relay before the
upload starts, L03 last (#1276). The running image owns that hook, so it protects uploads from the flash
after the one that installs it.

**Ship.** Use `make firmware-deploy`. It compiles the image on the host that runs it, so where it runs
follows Q-11 in [decisions](decisions.md): no exception is assumed, R3 asks Jason before it runs, and
nothing is compiled on the Mac until he answers. The preflight has no weekly or 48-hour rule
([decisions](decisions.md), 2026-11-05), so a fix-forward ships as soon as it is ready. Verify
with the `diagnostics.firmware_version` query in `docs/runbooks/laptop-operator.md` §3, then check that
`irr_program_id` shows `src=rom` before the first push and `src=nvs` after it. Once the first push is
confirmed and the first scheduled L01 run lands, run
`make firmware-promote-last-good FW_VERSION=<engine version>` (VD-51); until then a rollback flashes
pre-engine firmware, which the capability check in §6.1 reports honestly, and the bridge serves L01 and
Stop again (§6.5).

## 6. Platform changes

### 6.1 Compile and push: the ingestor's irrigation step

```text
irrigation write (dashboard / MCP) -> verdify_services.irrigation -> irrigation_* rows
                                   + activity_log (one txn) -> NOTIFY irrigation_changed
site value write (API-10 / set_setting) -> desired_state row -> NOTIFY desired_state_changed
ingestor irrigation_sync (ingestor/tasks/irrigation.py): every 5 s steps 0 and 1; steps 2-4 on either
wake and every 60 s:
  0. no engine services on this connection -> only the L01 bridge (§6.5); nothing else is sent
  1. irrigation_command rows: each stop first, as its own push request; then run, clear_fault, override
  2. compile_program(lines, slots, site values) in one REPEATABLE READ snapshot -> 584 B + sha256
  3. if it differs from the live irr_program_id and no stop is pending:
       esp32_push.push_irrigation_program(): begin -> 4 chunks -> commit
  4. irr_program_id matches -> irrigation_push.confirmed_at; new irr_events -> irrigation_ledger
```

- **Wake and cadence.** Every irrigation write sends `NOTIFY irrigation_changed` (empty payload) in its
  transaction. The ingestor's existing listener (`setpoint_listener`) also listens on that channel and
  sets `irrigation_sync_requested`, and it sets the same flag on `desired_state_changed` (site values).
  The task loop runs `irrigation_sync` every 5 s, which bounds command latency, and never force-launches
  it (`_FORCED_TASK_RETRY_MIN_S` would allow that once per 30 s): the pass reads the flag itself, clearing
  it before it reads, and runs steps 2-4 then or 60 s after its last full pass. A lost NOTIFY costs at
  most 60 s for the program and 5 s for a command. The task's timeout is 150 s, above
  `push_policy_transaction`'s 120 s budget, so it never cancels a push between begin and commit.
- **Same writer, same fences.** The step uses the same fences as every device write: writer lease,
  transport fence, the `push_policy_transaction` pattern (`ingestor/esp32_push.py:1307-1392`) and
  heap deferral below 30 kB free or an 18 kB largest block (`ingestor/tasks/_common.py:939-942`).
  Stop is never heap-deferred, and it goes out as its own push request before any program push in the
  same pass, so it overtakes an ordinary dispatch batch. No `setpoint_changes` rows, no NOTIFY payloads.
  Nothing halts: a failed push retries.
- **Engine capability.** `irrigation_sync` checks the services on the current native-API connection;
  on every connect the ingestor writes the answer to `system_state` as `irr_engine`, which the API and
  MCP read. With `irr_program_begin` or `irr_command` absent (pre-engine firmware, or a rollback to it),
  it sends no program and no command, logs once per connection, and API-IRR-01 reports `program.src`
  null with "engine not on controller". Run on lines other than L01, override and clear-fault
  (API-IRR-06, -12 and -13) then return 503 `unavailable` (not retryable; "arrives with the irrigation
  firmware") and store no command; run-now on L01 and every Stop go through the bridge (§6.5).
  Schedule, line and site writes are stored and pushed when the engine first appears.
- **Re-apply.** The 60 s check compares the desired hash with the live one. A controller that comes
  back with `src=rom` has its program again within a minute.
- **Rejects.** A device reject is stored in `irrigation_push.reject_code` and retried after 1, 5 and
  15 min. The Water screen shows "Not confirmed: <code>".
- **Commands.** A command row is `pending`, then `sent`, or `failed`. The ingestor marks it `sent` in
  the same transaction, before it calls the device, so each command is sent at most once. A command not
  sent within 2 min of its request ends `failed` ("too old") and is never sent, so a queued run never
  starts late. A stop is re-sent on each pass until the relays that were on at the request read OFF,
  for at most 2 min. Whether water started or stopped comes from the relay edge and `irr_state`, never
  from an ack.
- **Tasks and routes.** Add `irrigation_sync` to the ingestor `TASKS` table
  (`ingestor/ingestor.py:4485-4531`), with `push_irrigation_program()` and `send_irrigation_command()`
  in `ingestor/esp32_push.py`. Route `irr_program_id`, `irr_state` and `irr_events` to `system_state`
  in `ingestor/entity_map.py`.
- **As built (VD-42).** `ingestor/tasks/irrigation.py` (`_engine_pass`). Where the build settles what this
  section leaves open:
  - The text-sensor handler keeps the three values for the current connection in
    `shared.irrigation_readback` and every `irr_state` reading, in order, in `shared.irr_state_changes`, so
    a token that comes and goes while a pass is busy pushing still gets its ledger row. `""` is a reading
    (no token, no outcome), so a run's last token leaving is seen; a sensor that has not published yet is
    not. A new connection clears the values, and the pass compares the hash as soon as it reads one.
  - A Stop's "read OFF" is the native relay callback (`shared.wet_relay_read_off_since`, as the bridge
    reads it), the same callback that writes the `equipment_state` row, so it needs no query. A relay that
    read OFF once is done, so VPD misting may pulse again. A Stop goes even with no open relay in scope,
    because a mist window between pulses has none; one whose send failed goes again on the next pass.
  - A Stop fails only the pending runs in its scope requested before it, as designed here. The bridge
    keeps its own rule (every run pending when the Stop goes).
  - `run`, `clear_fault` and `override` also wait for this connection's first `irr_program_id`: until its
    first tick the engine logs and ignores a service call, so a run sent then would read `sent` and be
    lost. A Stop does not wait. A command whose `args` cannot make a call ends `failed` (`bad_args`), and
    one that provably never reached the transport ends `failed` with its reason; an unknown outcome (a
    timeout, a socket error) stays `sent`.
  - A reject is read at least 6 s after the commit goes out, because an accepted commit clears an old `j=`
    at once and a rejected one may publish nothing new. The retries are the delays between attempts: 1,
    5 and 15 min, then every 15 min. A push with neither a reject nor the hash after 30 s (the controller's
    NVS save failed) goes again on the next full pass; until then a wake never repeats it.
  - A re-push also clears `confirmed_at`: the controller is not running that hash when it is pushed again,
    so the row is unconfirmed until `irr_program_id` reports it. Confirming goes by the reported hash, whichever
    row holds it: a newer push that never landed must not leave the program the controller runs unconfirmed,
    because the fence alert checks nothing against an unconfirmed program.
  - Writes validate through `IrrigationPushRow`, `IrrigationLedgerRow` and `RunClose`
    (`verdify_schemas/irrigation.py`), M3's row models, which M3 left to the first issue that writes them.

### 6.2 Runs, outcomes and minutes by zone

- **Runs from relay edges.** `v_water_relay_runs` (migration 194) already turns wet-relay edges in
  `equipment_state` into runs. M4 adds `v_irrigation_minutes_daily` over it, adds L03
  (`fert_master_valve`), maps each relay to its line and zone (§3) with a fixed line map in the view, and
  takes gallons from the `water_meter_events` attribution. It does not use `water_systems`: its rows
  (migration 085) leave the wall drips without a zone and put fog in center. It needs no engine, so it
  ships first and covers the history back to 2025-08-05, the first wet-relay `equipment_state` row
  (2024-11-17 is the first grow-light row).
- **Cause.** In M4, cause comes from today's topology: L04, L07, L09 and fog are `vpd`, L02 and L03 are
  `feed`, and the rest are `unknown`. M3 (migration 283), which adds `irrigation_command`, extends the
  view: each run takes cause and slot from the newest `irrigation_ledger` row on its line that started
  at most 24 hours before it and whose span (to now while it is open) contains the run's start; an
  unmatched L01 run that starts within 30 s after the `sent_at` of a sent bridge run command (§6.5) is
  `run_now`; any other run keeps its M4 cause. No occurrence lasts 24 hours (a 60-pulse mist window
  with 600 s gaps and its 60 min tail, or a feed with its prewet, flush and waits, is under 16), and the
  bound keeps the per-run lookup on an index and keeps a row whose end never arrived from claiming
  later runs.
- **`irrigation_ledger`** (written by the ingestor): one row per engine occurrence or run-now, with
  `line_id`, `slot_id`, `local_day`, `cause` (`vpd`, `schedule`, `run_now`, `feed`, `prewet`, `flush`,
  `fallback`, `unknown`) and start from `irr_state`, which names them before the relay opens, and
  `outcome` and `reason` from `irr_events`, plus `command_id` and `actor`. Occurrences that never opened
  a valve (`skipped`, `missed`, `cap`) have `open_s = 0`. Gaps are filled by sequence number after a
  reconnect; an outcome that aged out of `irr_events` gets none, and the agenda shows "no outcome
  received", a gap, never `missed`. The cause and outcome enums live in
  `verdify_schemas/irrigation.py`, shared by the API, MCP and the web app.
- **Runs read (API-IRR-11).** One row per drip run, fertilizer stage or mist episode (pulses of one
  zone and cause with gaps of 10 min or less), from the runs joined to the ledger.
- **`v_irrigation_minutes_daily`.** Valve-open minutes, pulses and gallons per America/Denver day,
  split at local midnight (23- and 25-hour days are handled), by zone, line, cause and chemistry.
  Per-day coverage comes from `v_irrigation_day_coverage`, also in M4, built on `data_gaps` and the
  newest `climate` row: a day wholly inside a gap, or after the newest `climate` row (every day when
  there is no `climate` row at all), is `no_data`; today and any other day a gap touches are `partial`;
  the rest are `complete`. So days before climate's 365-day retention stay complete, and a day with no
  runs is told apart from a day with no data. Gaps show as gaps, never as zero; nothing is back-filled.
- **`fn_irrigation_minutes_daily(from, to)`.** The view rebuilds every run since the first edge on each
  read (about 1.4 s for 30 days on production in 2026-11, and more at two years of misting), so M4 also
  ships this reader: the view's rows for local days `from`..`to`, with the `equipment_state` scan bounded
  by the range. It is seeded with each relay's state at `from`'s local midnight, read as
  `v_water_relay_runs` reads it (any row on at the newest timestamp before it, rather than
  `fn_equip_at`'s single row), and a run still open at the end of the range looks ahead for its
  closing edge, so its gallons match the view's. It is SECURITY DEFINER and granted like the views.
  API-IRR-10 reads it, and API-IRR-01's minutes and MCP `irrigation_history` get the same numbers from
  it; a 30-day read is about 0.7 s on production and a 400-day read about 1.7 s. M3 redefines it with
  the view, and starts a run already on at `from` where the view starts it (the newest conflicting
  timestamp since the relay was last off, else the first timestamp after that), so the run matches
  the same ledger row and bridge command in both.
- **Gallons (D-IRP-18, Round 2b B), informational.** Whole gallons per run and per day: `gal` is the
  whole-gallon meter delta attributed to the run or mist episode (±1 gal; the meter also counts the
  hose, sink and hydro). There is no per-run rate and no flow flag; a run that moved no water reads
  "0 gal". These figures stop nothing. A day's `gal` sums `v_water_run_accounting.meter_attributed_gal`
  over the runs that start that day; when none of them overlapped an accepted delta it reads 0 if the
  meter recorded one that day and NULL if it recorded none. L03's valve minutes have no gallons: its
  water counts on the line it feeds.
- **As built (VD-40).** `verdify_services/irrigation/runs.py` builds runs from a window's own
  `equipment_state` rows, seeded as `fn_irrigation_minutes_daily` seeds them (any row on at the newest
  timestamp before the window, and a run already on keeps its true start) rather than with `fn_equip_at`'s
  single row, so API-IRR-01's today equals API-IRR-10's rows. The relay runs that join one ledger row are one
  run however far apart they are; a line's unjoined pulses of one cause with gaps of 10 min or less are one
  episode. A run's `gal` with no overlapping delta reads 0 when `climate.water_total_gal` has a reading from
  the run's start to 5 min past its end (the longest accepted delta) and null when it has none: the test is
  per run, so a meter gap inside a 31-day read stays a gap. A ledger row with no `started_at` (it opened no
  valve) lists at its occurrence's planned start, or when it was recorded if it has no slot (a refused
  run-now) or its schedule is gone, with `gal` 0. A run whose ledger row is still open stays out of API-IRR-11
  until the row ends or is 24 h old (the bound M3's join uses), because a mist window keeps its row open
  between pulses.
- **As built (VD-42).** `_sync_ledger` in `ingestor/tasks/irrigation.py`. Where the build settles what this
  section leaves open:
  - A token's identity is its line, cause, slot and start. Its end moves on a pause or resume and changes
    nothing. A token gone for 15 s (`equipment_state` is flushed every 5 s) closes its row, waiting up to
    2 min for an OFF edge the flush has not written. A token that comes back (a VPD pulse held the `m=`
    token, or occupancy blocked the window) is the same run: its row opens again and closes on all its
    edges. A VPD pulse on the relay inside the span counts toward it, as M3's join counts it.
  - `ended_at` is the last OFF edge. When the token left across a reconnect the reconnect's own relay row is
    not an edge, so it is the token's planned end (no later than the reconnect). After an ingestor restart
    the planned end is gone and the OFF edge stands.
  - A slot's `local_day` is the day `irr_events` names, the day its weekday matched: a solar start past
    midnight or a late start opens the next morning and belongs to the day before (`occurrence_day`).
  - A fertilizer run's flush starts after its outcome is published, so its row takes the outcome of its
    occurrence's other rows (a run-now's: of the rows that share its command).
  - An entry counts as stored when a row holds its sequence, slot and day, not when it is older than the
    newest stored sequence: a power cut zeroes the RTC block and restarts the sequence, and comparing with
    the old newest would drop every outcome for thousands of runs. A row that has an outcome keeps it.
  - A run-now's outcome finds the newest row on its line and local day with no outcome that opened a valve
    and is not still running; entries are applied newest first, so two in one text find their own rows. A
    `skipped` or `missed` run-now opened nothing and gets its own row.

### 6.3 Alerts (in-app `alert_log`, category `water`)

- **Retarget `irrigation_fence_breach`** (`ingestor/tasks/alerts.py:461-530`). Today it fires when
  certain relays turn on at all. In v2 it fires when a relay turns on while its line is disabled in the
  applied program (the decoded `irrigation_push` row whose hash `irr_program_id` reports), or when L03
  opens with no fertilizer run. Without this change, enabling L06, L08 or L10 would raise false alerts.
- **New `irrigation_run_missed` (warning).** It fires when an enabled schedule's occurrence ends
  `missed`, or has no outcome 2 h after its planned start under a confirmed program, and only when the
  ingestor watched the whole window: no reconnect (`irr_engine` row, §6.1) and no `climate` gap over
  300 s in it. A `degraded_only` slot that does not fire raises nothing.
- **New `irrigation_line_faulted` (warning).** It fires when a mister zone latches (F10) and resolves
  when the latch clears. A back-off alone raises nothing.
- Both new types go in `verdify_schemas/alerts.py`. Nothing else is new: the leak and heap alerts
  already exist, and with no leak latch (Round 2 #3) and no leak force-off in the engine (F14), the
  leak alert is the whole leak response.
- **As built (VD-42).** `ingestor/tasks/alerts.py`. All three need an `irr_engine` row reading `true`, and
  the new two an `irr_program_id` reading, so nothing new appears before the engine or after a rollback.
  The applied program is the newest push of the reported hash; with none (or on `src=rom`) today's three
  fence rules stay, and an unconfirmed one checks nothing until it is confirmed. For `irrigation_run_missed`
  an occurrence with an open ledger row is still running, and one that opened and closed with no outcome
  counts only 2 h past its planned start plus its planned span (prewet, pulses and gaps), with the window
  watched to there: a long mist window can be blocked with its token gone and still have pulses to deliver.
  One with no row counts at the 2 h mark. A slot the compiler left off the controller raises nothing, and a
  reported miss whose schedule is gone has no window to check, so it raises nothing either. A failed read
  keeps every open water alert open, as unknown is not recovery.
- **The leak alert outlives the flag (VD-72).** While `leak_detected` is on it reads "Water flowing
  with every Verdify valve closed since HH:MM". Once the flag clears the row stays open: after two
  meter readings under 0.1 gal/min it reads "Flow stopped at HH:MM" (America/Denver;
  `details.stopped_at`), flow that starts again puts the flowing text back on the same row, and only
  a person or agent resolve (API-16, MCP `alerts` or Slack) closes it. It stays `critical`.

### 6.4 Iris context

Iris's context pack, section 11 (`scripts/gather-plan-context.sh:435-470` and its copy in
`deploy/k8s/components/ingestor-gather-script/gather-script-configmap.yaml`), reads the retired views
`v_irrigation_schedule_current` and `v_irrigation_fertigation_runs`. It becomes the output of a new
entry point in the ingestor image, `python3 -m tasks.irrigation context --hours 24`, run from the image's
working directory (`/app/ingestor`, where `ingestor` is `ingestor.py`, not a package). That reads
the same M3 line-state view as `verdify_services.irrigation` status and the `verdify_schemas.irrigation`
agenda, and prints, in about 60 lines at most: one row per line (enabled, flags, `manual`,
`paused_until`, fault, today's minutes against the cap, the last outcome); the next 24 h of
occurrences; the manual schedules and lines, with who set them and since when; and whether an
experiment is armed. The prompt text about direct-wet windows (`ingestor/iris_planner.py:531-541`)
is updated to match, and it says that the irrigation tools change watering only and do not count as
Iris's plan for a trigger. `make planner-dry` renders the section, and the PR reports the change in
plan-cycle cost.

### 6.5 L01 before the engine: the run-now bridge

L01 goes first (Round 2). Until the engine firmware is on the controller, run-now on L01 (API-IRR-06,
MCP `irrigation_run`) presses the existing button `btn_wall_clean` through the ingestor's single ESPHome
session, under the same writer fences; the run lasts the controller's `irrig_wall_duration_min` (a
Settings row while the bridge lasts), so the request carries no minutes. Stop, on any line or all
(API-IRR-07, MCP `irrigation_stop`), turns the open wet relays in its scope off the same way (their
guards block only turn-on). A Stop that covers L01 first ends the legacy job: the bridge turns
`sw_irrig_enabled` off, waits one 5 s tick for `cancel_all` (`controls.yaml:2479-2492`) to close the
valves and clear the queue, and turns it back on; otherwise the job's timer keeps fog and the misters
off (`sw_irrig_wall_enabled` cancels only a weekly feed). After a Stop, VPD misting may pulse again,
and the Stop result says so. A Stop also fails every pending run in its scope, so a run requested just
before it is never pressed after it. A press during a run would queue a second full run, so the bridge
refuses run-now with `line_busy` while the wall relay is on or before the last run's planned end. It
applies the engine path's `line_disabled` and `line_paused` checks, and also refuses `line_disabled`
while the controller's `sw_irrig_enabled` or `sw_irrig_wall_enabled` reads off, because the job machine
drops the press then. A press during a live leak reads "Not started": today's interlock cancels it
(`controls.yaml:2520`), so no relay edge follows. Commands, `activity_log` rows and the `run_now` cause
work as with the engine. The bridge stays as the fallback until VD-51 promotes the engine to last-good,
because until then a rollback flashes pre-engine firmware (§5 Ship). Its registry rows and routes (the
`sw_irrig_*` switches and their aliases, the two `irrig_*_duration_min` rows and the `irrigation_*`
switch routes) retire in VD-47's follow-up, merged after that promotion. L01 is the first line
scheduled once the engine runs.

**As built (VD-64).** `verdify_services.irrigation.writes` stores the commands; step 0 of
`irrigation_sync` (`ingestor/tasks/irrigation.py`) sends them through `esp32_push.press_wall_clean`,
`bridge_stop`, `bridge_switch_off` and `bridge_set_irrigation_enabled`. Where the build settles what this
section leaves open:

- While `irr_engine` reads true, run-now takes the engine path (VD-41, §8.3), and Stop is still stored.
- `line_busy` lasts to the planned end of the newest pending or sent L01 run, or less when a later Stop
  covering L01 was requested; with only the relay ON, `retry_after_s` counts from its ON edge plus
  `irrig_wall_duration_min`.
- The ingestor also refuses a press (`line_busy`) while an earlier bridge run has not reached its planned
  end. A press that never reached the transport ends `failed` with its reason; one whose outcome is
  unknown stays `sent`.
- `args.relays_on` holds the relays in scope not read OFF on the current connection, so a Stop in the state
  burst after a reconnect also switches off the relays not read yet.
- `sw_irrig_enabled` is toggled only when the switch's own state reads on, through its `sw_irrigation_enabled`
  alias row: the `cfg_irrig_enabled` readback publishes every 30 s and reads the old value around a toggle. A
  Stop before that state has been read on the connection closes its relays without ending the job, so the job's
  timer holds fog and the misters until it runs out. A cut-short toggle is restored for up to a day after the Stop.
- A `setpoint_snapshot` row taken during the toggle can read `sw_irrig_enabled` off for a minute, so for 2 min
  after a Stop that toggled it run-now ignores that reading; `sw_irrig_wall_enabled` still refuses.
- A row the model refuses is logged and dropped by the pass, so it never keeps a Stop from going out, and it
  expires like any other.
- On L01 a run body with mister or rinse fields is 400 `invalid_request`; `trigger_id` and
  `planner_instance` are 1-64 characters.

**As built (VD-47's follow-up, merged by VD-51 after the promotion).** The bridge's eleven registry rows, their
setpoint and readback routes and the four `irrigation_*` switch routes are gone; their names are in
`RETIRED_UNROUTABLE_PARAMS`, so a stale plan or desired row naming one is skipped, and Settings answers
`unknown_target` for them. Migration 288 had already made the four `equipment.irrigation_*` sensor rows inactive.
The bridge's code stays, though nothing selects it while every connection reports `irr_engine` true:

- run-now on L01 keeps the pre-engine firmware's default duration (10 min, 1-120) in the service and reads the last
  readbacks recorded before the engine flash, because no route records new ones;
- `sw_irrig_enabled`'s state no longer reaches the ingestor, so a Stop closes its relays without the toggle, as
  one in the state burst after a reconnect always did; refusal details no longer point to Settings rows.

## 7. Home Assistant decommission

Home Assistant keeps occupancy data, Lutron switch control and, until a direct reader with no Home
Assistant replaces it (Round 2), the YINMIK hydroponic probe read. Everything else goes.

| ID | Touchpoint | Where | Replacement |
|---|---|---|---|
| HA-01 | RUN CLEAN buttons (wall, center) | `firmware/greenhouse/tunables.yaml:2410-2425` | Run-now from the Water screen and `irrigation_run` (F4); before the engine, the L01 bridge presses the wall button (§6.5) |
| HA-02 | Buttons Restart, Fan Cycle, Vent Bypass Toggle and Fog Cycle, and their scripts | `firmware/greenhouse.yaml:736-784` and the scripts they call (from `:785`) | `manual_override` (F12) through API-IRR-12 |
| HA-03 | Buttons Manual FANS, HUMIDIFY and VENT-BYPASS | `firmware/greenhouse/hardware.yaml:815-878` | The same service, driving the same latches; the physical push buttons toggle them too |
| HA-04 | Controller switches mirrored from HA into `equipment_state` (irrigation enables, economiser, fog-closes-vent) | `ingestor/tasks/_common.py:880-887`; `ingestor/tasks/ha.py:607-626` | The native-API stream already carries device state; v2 enables come from `irrigation_line` and are confirmed by the program hash |
| HA-05 | YINMIK hydroponic probe via HA template sensors | `_common.py:813-826`; `ha.py:548-582` | Stays until a direct reader with no Home Assistant replaces it (its own issue); the `hydro_*` columns stay live |
| HA-06 | Center root-zone feedback aliases via HA | `_common.py:829-871`; `ha.py:548-582` | Future probes join the ESP32 Modbus bus, like `south_1_soil_probe` (`firmware/greenhouse/sensors.yaml:459-500`) |
| HA-07 | Tempest outdoor weather via HA | `ingestor/tasks/ha.py:470-525`; `_common.py:786-807`; task at `ingestor/ingestor.py:4491` | Nothing to replace: the ESP32 already receives the Tempest's UDP broadcasts on port 50222 (`firmware/greenhouse/external_sensors.yaml`) and the ingestor maps them (`ingestor/entity_map.py:92`). Remove the redundant task and its map; the HA-only derived fields (feels-like, wet bulb, wind averages, air density) become gaps |
| HA-08 | Legacy VM sync scripts | `scripts/ha-sensor-sync.py`, `scripts/tempest-sync.py` and their tests | Deleted, with their entries in the CI test lists |
| HA-09 | Hourly HA recorder backfill CronJob | `deploy/k8s/components/ha-gap-backfill/`; `deploy/k8s/overlays/prod/kustomization.yaml:175` | None. Gaps stay gaps; the ingestor's observation spool covers short database outages. Prune is off, so after the sync delete CronJob `verdify-ha-gap-backfill`, its script ConfigMap and NetworkPolicy `allow-db-from-ha-gap-backfill`, and drop it from `deploy/k8s/overlays/prod/egress-allow-management.yaml` and `tests/test_egress_deny_management.py`. Its login, Secret and bootstrap duty stay (unused is harmless) |
| HA-10 | Iris context reads HA `/api/states` for tunable min/max | `scripts/gather-plan-context.sh:759-783, 1630-1636`; the configmap copy (`gather-script-configmap.yaml:96-97, 796-818, 1665-1670`) | Bounds from `verdify_schemas/tunable_registry.py` |
| HA-11 | MQTT on the HAOS broker: the feedback topics and the unused occupancy topic `sentinel/occupancy/greenhouse_zone` | `ingestor/entity_map.py:183-239`; `mqtt_loop` at `ingestor/ingestor.py:4679-4737` (subscription at `:4694`), started at `:5401` | Feedback: probes on the ESP32 Modbus bus. Occupancy: the HA sensor below is the only input (Round 2b A). `mqtt_loop` and its feed into `ingestor/occupancy.py` go |
| HA-12 | HA lookups in the feedback validator | `scripts/validate-irrigation-feedback.py:71-125, 514-582` (HA) and `:192-195, 713-835` (MQTT) | Native-API candidates only. The MQTT candidates (`MQTT_FEEDBACK_CANDIDATES`, `ingestor/entity_map.py:241-303`), which subscribe to the same HAOS broker, go with HA-11 (VD-48), together with `scripts/clear-irrigation-stale-retained.py` and the HA and MQTT discovery flags of the `irrigation-feedback-*` make targets |
| HA-13 | HA control of the controller: the Greenhouse dashboard's controller tiles and the controller's control entities | jvallery/homeassistant `dashboards/hydroponics.yaml:94-130` (the "Automatic control" tiles of the live Greenhouse dashboard); the unloaded captures `iac-captured/dashboards/hydroponics_dashboard.yaml` and `iac-captured/dashboards/irrigation_control.yaml` (the yard dashboard, with one greenhouse card); the HA entity registry | Filed in jvallery/homeassistant, its owner. dashboard.verdify.ai replaces the controls. Delete the tiles and `hydroponics_dashboard.yaml`, and only the greenhouse card of `irrigation_control.yaml`; its yard views stay. Disable the controller's switch, number and button entities, the grow-light proxies `switch.greenhouse_grow_light_{main,grow}` included unless a live consumer turns up (none exists in jvallery/homeassistant today). Keep enabled: the controller's sensors and binary sensors (monitoring-stack reads `binary_sensor.greenhouse_api_status`, the south temperature and RH and the south-2 and west soil moisture), the Lutron `switch.greenhouse_main` and `switch.greenhouse_grow`, the Frigate `binary_sensor.greenhouse_zone_person_occupancy` and the YINMIK hydro sensors. HA keeps the ESPHome connection with actions allowed, so the firmware's `homeassistant.service` calls still reach Lutron |

Three things stay.

| Kept | Where |
|---|---|
| Occupancy | `binary_sensor.greenhouse_zone_person_occupancy` (Frigate via HA, polled every 300 s; `_common.py:890-892`, `ha.py:628-651`) is the only input; the MQTT path goes (HA-11). It feeds `ingestor/occupancy.py`, which pushes to the ESP32. While HA reports the sensor ON and available, every poll re-extends the 15 min latch (`OCCUPANCY_LATCH_MIN`; today only an OFF-to-ON edge does), up to 60 min after the visit's first detection; OFF releases at once |
| Lutron | The firmware grow lights calling `homeassistant.service` on `switch.greenhouse_main` and `switch.greenhouse_grow` (`hardware.yaml:644-685`); the `verdify-setpoint-server` writer (`scripts/setpoint-server.py`); the light readback `_LIGHT_ENTITIES` (`_common.py:874-877`); the HA token Secret and `HA_URL`. The controller's own grow-light switches, which share the Lutron keys today, move to `grow_light_main_intent` and `grow_light_grow_intent` in `EQUIPMENT_SWITCH_MAP`, so `equipment_state`'s `grow_light_main` and `grow_light_grow` are only the Lutron readback; Equipment shows both |
| Hydroponic probe | `_HYDRO_MAP` (`_common.py:813-826`), read by `ha_sensor_sync` until the direct reader lands |

After the cleanup, `ha_sensor_sync` reads only lights, occupancy and the hydro probe. Update
`docs/SERVICE_MAP.md`, `docs/operations/HEALTH_COVERAGE_KEYS.md` and `scripts/health-check.sh:180-189`
(its outdoor check names the ESP32 Tempest path) in the same PRs.

As built (VD-48): the two `grow_light_*_intent` streams are outside the stream list of the durable
equipment receipt function (`fn_record_equipment_state_source_receipt`, migration 216), which rejects any
other stream, so the ingestor writes them through the ordinary telemetry view with no continuity receipt.
If Home Assistant ever omits the occupancy sensor's `last_changed`, the first ON poll latches once and later
ON polls do not re-extend it.

## 8. API operations and MCP tools

### 8.1 Private API (`verdify-operator-api`, `/v1`)

The browser reaches these as `/api/v1/...` through the BFF; they are never on the public edge. Viewers
can use the reads; writes need the operator role. The ids are the OpenAPI `operationId`s. Site values
are written through the Settings write (API-10).

| Id | Operation | Purpose |
|---|---|---|
| API-IRR-01 | `GET /v1/irrigation` | Lines (state with reason and age, enabled, check, flags, VPD source, paused, fault and its end time, when it last watered), the active run on each manifold, the next run per line, program sync (desired and applied hash, revision, last reject, `src`), today's minutes by zone, cap use, the live leak state and occupancy |
| API-IRR-02 | `GET /v1/irrigation/schedules?days=1..7&past_days=0..7` | Schedules with their sync state; the week of occurrences per line and day, planned against actual (ran, skipped with why, missed, blocked with why); solar times for the draft preview; the bounds per line kind |
| API-IRR-03 | `POST /v1/irrigation/schedules` | Create a schedule |
| API-IRR-04 | `PUT /v1/irrigation/schedules/{slot_id}` | Edit, enable or disable a schedule; set `manual` |
| API-IRR-05 | `DELETE /v1/irrigation/schedules/{slot_id}` | Delete a schedule |
| API-IRR-06 | `POST /v1/irrigation/lines/{line_id}/run` | Drips `{minutes}`; fertilizer drips (L02, L06) `{minutes, prewet_s, flush_s}`; misters `{on_s, count, gap_s}`; fertilizer misters (L08, L10) `{on_s, count, gap_s, prewet_s, flush_s}`, within the mister bounds of §4.3. Returns 202 with the command. Before the engine, L01 only (§6.5) |
| API-IRR-07 | `POST /v1/irrigation/stop` | `{line_id}`, or omit it for every line. Returns 202; never blocked or deferred |
| API-IRR-08 | `PUT /v1/irrigation/lines/{line_id}` | `enabled`, `checked` and `check_note` (dashboard only), `vpd_member` and `schedule_member` (L04, L07, L09), `daily_cap_min`, `vpd_reserve_min`, `paused_until`, `manual`. `line_id = all` accepts only `paused_until`, for pausing or resuming every line |
| API-IRR-09 | (reserved) | Mister flags moved into API-IRR-08 |
| API-IRR-10 | `GET /v1/irrigation/minutes?from&to&group_by=zone\|line\|cause` | Minutes, pulses and gallons per day, at most 400 days |
| API-IRR-11 | `GET /v1/irrigation/runs?from&to&line&cause&outcome` | Runs and outcomes, at most 31 days per page |
| API-IRR-12 | `POST /v1/equipment/override` | `{kind: fans\|humidify\|vent_bypass\|clear\|restart, minutes}`. The replacement for the HA buttons (HA-02, HA-03); engine release only. Returns 202 |
| API-IRR-13 | `POST /v1/irrigation/clear-fault` | `{line_id}`, or omit it for every mister: clears a mister watchdog latch (D-IRP-19). Returns 202 |

As built (VD-40; `verdify_services/irrigation/reads.py`, with `runs.py` and `program.py` beside it):

- **Waiting or blocked.** A `w=` token with `man`, `l03`, `occ` or `vok` reads `waiting`: the run goes by itself
  once the manifold or L03 frees, nobody is seen ("Waiting: someone inside") or VPD sensing degrades. Every other
  code names a rail that holds the run (a limit, a cap, a sensor fault, the watchdog's back-off or latch) and reads
  `blocked`. Its `until_epoch`, when known, is `LineState.ends_at`.
- **Line state with no reading.** With the engine, an `irr_state` row that is missing or outside the grammar
  is no reading, so the line reads its relay as before the engine.
- **While the controller is not reporting.** Beside the unknown line states, `active_runs` is empty,
  `fallback_active` false and `occupancy.hold_enabled` null: an old token says nothing about now.
- **A day with no data.** With no `climate` row today, each zone's and line's `today` is null, as API-IRR-10
  reads that day `no_data`; a zone with no enabled line is null whatever its relays did today.
- **Program with no engine.** `applied_hash` and `applied_revision` are null too, whatever an old
  `irr_program_id` row says.
- **`next_run`** looks 8 days ahead, so a weekly schedule past its time today still has one. A `degraded_only`
  occurrence counts, as the agenda marks it planned; its `label` (`sensor-fault backup`) says what it is.
- **Agenda and pauses.** An occurrence `agenda()` skips for today's `paused_until` but with no pause row in
  force at its start was not skipped for that pause, so a past one reads as planned. A future one with no row
  at all reads "paused". Today's enable and membership flags say nothing about a past start either: with the
  engine in effect at it, its ledger row answers, and with none it reads `done` with `unknown`. With no engine
  in effect it keeps the agenda's skip.
- **`Schedule`.** A row with no `label` or `reason` leaves the field out, because `ScheduleInput` types them
  as strings.

### 8.2 MCP tools (one surface for every agent)

Every agent uses the same tools: `iris`, `agent` (`agent:<name>`) and `admin`
([architecture](architecture.md) §5.6 holds the audience table). They are thin adapters over
`verdify_services.irrigation`, return the REST JSON (the same Pydantic models) and need no planner
trigger. Site values use `set_setting`.

| Tool | Arguments (defaults) | Same as |
|---|---|---|
| `irrigation_status` | none | API-IRR-01 |
| `irrigation_schedule` | `days` 7 (1-7), `past_days` 0 (0-7) | API-IRR-02, with the bounds per line kind and `max_schedules` 24 |
| `irrigation_history` | `view` (`minutes` or `runs`), `since`, `until` (the REST `from` and `to`; `from` is a Python keyword), `group_by` (`zone`), `line`, `cause`, `outcome`, `cursor` | API-IRR-10 (up to 400 days) or API-IRR-11 (up to 31 days) |
| `irrigation_schedule_set` | `slot_id` (null creates), `delete` (false), `line_id`, `days`, `start`, `on_s`, `count`, `gap_s`, `prewet_s`, `flush_s`, `degraded_only`, `enabled`, `label`, `manual` (false), `reason`, `request_id`, `trigger_id`, `planner_instance`; omitted fields keep their value, except `manual`, which an edit stores as false unless passed | API-IRR-03 to API-IRR-05 |
| `irrigation_run` | `line_id`, then `minutes` (drips) or `on_s`, `count` and `gap_s` (misters), with `prewet_s` and `flush_s` added on fertilizer lines (L02 and L06 take `minutes`; L08 and L10 take the mister fields); `reason`, `request_id`, `trigger_id`, `planner_instance` | API-IRR-06 |
| `irrigation_stop` | `line_id` (null for every line), `reason`, `request_id` | API-IRR-07; any agent, any time |
| `irrigation_line_set` | `line_id` (or `all`), `enabled`, `vpd_member`, `schedule_member`, `daily_cap_min`, `vpd_reserve_min`, `paused_until`, `manual` (false; applied only when the call carries a definition field, so a pause or resume never changes it), `reason`, `request_id`, `trigger_id`, `planner_instance` | API-IRR-08 |
| `irrigation_clear_fault` | `line_id` (null for every mister), `reason`, `request_id` | API-IRR-13 |

- **Typed and self-describing.** Arguments are typed, with enums for `line_id`, `start.kind`, `view` and
  `group_by`, never a JSON string. A refusal returns the Problem JSON with MCP `isError` set; a value
  outside the tool's schema (an enum, a bound) is refused by the MCP SDK before the tool runs, with
  `isError` and a plain message. Each description names its REST twin, its `manual` default and its
  refusal codes. The read tools join `_TOOL_READ_ONLY`, and existing tools keep their signatures.
- **People only.** No tool takes `checked` or `check_note` (§4.2). The API-IRR-12 bursts have no MCP
  tool in MVP.
- **Not a plan.** `trigger_id` and `planner_instance`, both optional, are stored only in the
  `activity_log` detail. These tools never read, lock or update `plan_delivery_log` or
  `planner_trigger_ledger`, so they never satisfy or fail a planning trigger; Iris still ends every
  trigger with `set_plan` or `acknowledge_trigger`. Each description says "Changes watering only; it
  does not count as your plan for this trigger."
- **Lockstep.** Each tool gets a `TOOL_AUDIENCES` entry in `mcp/server.py` (startup fails without one)
  and joins Iris's lists: `HERMES_REQUIRED_TOOLS`, `hermes/iris/config.yaml`,
  `deploy/k8s/components/hermes-iris/hermes-config.yaml` and `tests/test_mcp_audience_auth.py`, checked
  with `make planner-dry`. The server instructions in `mcp/server.py` gain one paragraph on these
  tools, `manual`, `request_id`, the refusal codes and "irrigation_stop always works".

As built (VD-61; `mcp/server.py`). Where the build settles what this section leaves open:

- **`paused_until`** is an ISO date-time with its UTC offset. A time at or before now resumes the line, as REST's null
  or now does. The tool has no null for it: leaving it out changes nothing.
- **Delete.** `delete=true` answers `{"slot_id": <id>, "deleted": true, "manual_before": <flag>}`, because REST answers
  204 with no body. Without a `slot_id`, or with a schedule field (`manual: true` included), it is `invalid_request`,
  before any connection opens.
- **`manual` on a line.** The tool follows REST's `line_edit_manual` with false for its default. A definition field
  passes the flag the call carries, false when omitted. `paused_until` on one line with no definition field passes
  none, even with `manual` beside it, so a pause never changes the flag. `manual` with no other field sets it, and
  `all` passes what it was sent, so the service refuses `manual` beside `all`.
- **Run with the engine.** A drip needs `minutes`: `irrigation_run('L01')` with none is `invalid_request` naming it.
  Only the bridge runs L01 for `irrig_wall_duration_min` with `minutes` left out.
- **Schemas.** `irrigation_schedule_set` takes L01, L02 and L04-L10 and `irrigation_clear_fault` takes L04, L07 and L09,
  so the SDK refuses another line before the tool runs; `unknown_target` is then a schedule that does not exist. Numbers
  are integers within the widest bounds of any line kind, so a fractional one is refused too, where a REST body snaps
  it; `validate_schedule` holds each line kind to its own, as `out_of_range`.
- **Triggers.** `trigger_id` and `planner_instance` are free text of 1-64 characters, stored in the activity detail and
  never looked up, so an unknown id is not a refusal.
- **Hermes.** All eight tools are in `PLAN_OPTIONAL` in `hermes-config.yaml`: Hermes stays ready on an MCP image from
  before them, because Iris ends every trigger without them.

### 8.3 Write rules (API and MCP alike)

- **Validation on submit.** The checks are those in §4.3, the run-now refusals in §4.7,
  `check_required` (§4.2) and `unknown_target`. A rejection is JSON with the `code`, `detail`, the
  bounds, the current value and `retryable`, which follows the OpenAPI rule: true only for `line_busy`
  and `manifold_busy` (with `retry_after_s` when `irr_state` knows it), `query_timeout` and
  `unavailable`, and false for the pre-engine `unavailable` ("arrives with the irrigation firmware",
  §6.1). An acceptance returns the stored object after snapping.
- **One writer at a time.** Every irrigation write takes
  `pg_advisory_xact_lock(hashtext('irrigation:' || greenhouse_id))` before its cross-row checks, so two
  writers can never both take the 24th slot.
- **Manual.** A definition edit (schedule editor, line options, enables, mister switches) stores the
  flag it carries. A REST create or definition edit that omits it stores true; the dashboard always
  sends it, with its "Manual: Iris skips this" box ticked by default. MCP creates and definition edits
  default to false. Pause, resume, run-now, stop and clear-fault never carry or change it. Only Iris is
  refused with `manual`, on any schedule or line write to a manual item, a pause included (for `all`,
  while any line is manual); run, stop, clear-fault and override never check the flag. A person or
  another agent may overwrite it, and `manual_before` in the response and the activity detail show when
  that cleared the flag.
- **Idempotency and attribution.** Every write accepts an `Idempotency-Key` (MCP `request_id`),
  stored as `request_id` on its one `activity_log` row together with a hash of the request. The same
  source, key and payload return the stored object again: REST answers with the header
  `Idempotency-Replayed: true`, and MCP returns `replayed: true`. The same key with a different payload
  returns 422 `request_id_reused`. Every write records the source (`dashboard`, `iris`, `agent:<name>`
  or `system`), the actor (the Authentik username, `iris`, the agent name or `forecast-engine`), the
  time and an optional reason. While an experiment is armed ([architecture](architecture.md) §5.2),
  the write proceeds and its row carries the experiment id; nothing is refused (Round 2).
- **The writes switch.** The BFF enforces `DASHBOARD_WRITES_ENABLED` and never blocks Stop.
  `verdify_services` and MCP never read it, so it never gates agent writes.
- **Readback.** A schedule, line or site write reads REQUESTED until the device hash matches a program
  compiled after it, then CONFIRMED. After 5 min without a match it reads NOT CONFIRMED. Before the
  engine firmware it stays REQUESTED and never reads NOT CONFIRMED: a schedule, line or site write reads
  "applies with the irrigation firmware" (`no_engine`, §4.1), the occurrences then planned read
  `blocked` ("arrives with the irrigation firmware"), and `next_run` is null. A command returns 202 with
  status `pending`, then `sent` (or `failed` when too old); the relay edge and `irr_state` show whether
  water started or stopped.

As built (VD-41; `verdify_services/irrigation/writes.py` and `api/operator_routes/irrigation_writes.py`). Where the
build settles what this section and §4.7 leave open:

- **The lock comes first**, before the replay read, as `recorded_write`'s `lock`. So a duplicate sent while the first
  is still in flight waits and replays it; a read before the lock would have sent it on to the checks, to be refused by
  what the first just stored (`too_close`, `line_busy`, `unknown_target`).
- **Run-now checks** run in the order §4.7's API rules list them, after the body per line kind: `line_disabled`,
  `line_paused`, `line_faulted`, `not_schedule_member`, `line_busy`, `manifold_busy`, `cap_reached`.
- **One run at a time.** The engine holds one run, whichever manifold it is on (`irr_run_now`), so another line's
  run token that is not a VPD pulse is `manifold_busy` on either manifold; the API refuses at once what the device
  would refuse. The detail names the line and its planned end on the America/Denver clock, and `current` holds the
  end as a time.
- **`line_faulted`** is a latch (`f=...,l`) on any line of the mister's zone, as `zone_lines()` does on the device: a
  latch on L08 refuses L07 too. A back-off refuses nothing.
- **`line_busy`, requested.** A pending run older than the 2 min send window never counts, and neither does a run
  ended by a later Stop that covers its line.
- **`cap_reached`** reads today's minutes through `fn_irrigation_minutes_daily`, M4's reader that holds the
  `equipment_state` scan to the day, because it runs under the write lock. A day with no data counts 0, and the device
  still enforces its own counters.
- **What the device still refuses.** The API checks the requested line only. A fertilizer run whose prewet or flush
  would open a disabled clean partner returns 202 and ends `skipped` ("line disabled") on the device, so enable L07
  or L09 before running L08 or L10 with a rinse.
- **The check.** `enabled: true` or `schedule_member: true` needs a check whatever the line reads now, so a line
  enabled without one (the L01 and L04 seeds) is refused the same way. `check_note` without `checked: true` is 400
  `invalid_request`. `checked: false` records nothing, and only a dashboard actor may send it. A new check replaces
  the old one's who, when and note.
- **`manual` on REST.** A body with `paused_until` or the check and no definition field passes no flag even if it
  carries `manual`, so a pause never changes it. A body with only `manual` and a reason sets it. An empty body or one
  with only a reason passes none and updates only the row's attribution.
- **`manual` in the services.** A create with no flag is 400 `invalid_request`: the services never default it.
- **`line_id` `all`** needs `paused_until` (400 without it), and a `manual` beside it is 400 too. Its activity row
  keeps each line's old pause and flag in `detail.lines_before`.
- **Activity detail.** The values after a write are `detail.value`, beside `detail.before`, as the Activity screen
  and the settings writes read them. A schedule write holds the whole schedule on both sides; a line write holds the
  fields the request carried. A create's `target` is its new `slot_id`.
- **Replays.** A schedule's replay answers from its activity row, so it works after the schedule was deleted; a
  line's reads the line as it is now.
- **Override.** `args` is `{kind, minutes}` for a burst and `{kind}` for `clear` and `restart`, where `minutes` is 400
  `invalid_request`. The activity `target` is the kind. Run-now's `args` are the body per line kind as sent.
- **Delete on MCP.** REST answers 204; `schedule_delete` returns `{slot_id, deleted, manual_before}` for the MCP tool.
- **Bounds.** A schedule's or a line's number outside its bounds is 422 `out_of_range`. The REST bodies check only
  the types, so only `validate_schedule` and `validate_line` answer with `min` and `max`. Run-now's bounds stay
  `RunRequest`'s (400). Fractional numbers snap to whole ones in all of them.
- **Identity fields** (`actor`, `source`, `*_by`) in a body are dropped unread; any other unknown field is 400.
- **What the database cannot hold** is 400 `invalid_request`, not a server error: a NUL character in a label, check
  note, reason, `trigger_id` or `planner_instance`, and a `paused_until` with no UTC form (the last hours of year 9999
  behind UTC). A pause further ahead than the record's 32-bit time (2106) is stored, and the compiler sends that limit.

## 9. Water screen

The screen answers four questions without a click: what is watering now, and why; what runs next; what
is stopping water; and how many minutes each zone got. Layout and states are in [screens](screens.md);
look and charts follow [design system](design-system.md), with uPlot for charts.

| Shows | Source |
|---|---|
| One card per line (L01, L02 with L03, L04-L10): CLEAN or FERT, enabled, live state (idle, running, waiting or blocked, with the reason in words), when it last watered, today's minutes, next run, last outcome, and "No check recorded" until a person records one. L05 and L06 read RESERVED and stay disabled until a person enables them. Fog (L11) reads "climate only" | API-IRR-01 |
| Limits strip: SAF-4 used out of 360 gal, soft budget, each mister zone's minutes against its duty cap with the reserve marked, drip line caps, the live leak state, occupancy ("someone inside, seen N min ago") | API-IRR-01 |
| Program banner: "On controller: rev 42, confirmed 2 min ago", or "Not confirmed: <reason>" | API-IRR-01 |
| Mister zones: the VPD and Schedule switches (confirmed by the program hash), any back-off or latch with its end time, and the VPD source (own, peer or house) | API-IRR-01 |
| Minutes by zone for 7 or 30 days, stacked by cause, clean below fertilizer, each zone's cap as a tick; fog shown as humidification minutes | API-IRR-10 |
| Week view: one row per line and one column per day (today and the next 6, with a "Last 7 days" toggle), occurrences as blocks with their time and minutes or pulses, skipped ones hatched with why, past ones with their outcome; a draft schedule previews in place | API-IRR-02 |
| Runs: cause, actor, minutes (pulses for mist), whole gallons ("0 gal" when none moved), outcome | API-IRR-11 |

| Does (operator; viewers get the same screen read-only) | Calls |
|---|---|
| Run now on a line; Stop on a line and Stop all water, always visible and one tap | API-IRR-06, API-IRR-07 |
| Pause one or all lines until a time, or resume | API-IRR-08 |
| Enable or disable a line, ticking Checked when needed; switch VPD and Schedule on misters | API-IRR-08 |
| Clear a latched mister | API-IRR-13 |
| Add, edit or delete a schedule, previewed in the week view, with the slot count out of 24 and inline validation | API-IRR-03 to API-IRR-05 |
| Edit caps | API-IRR-08; SAF-4, RH ceiling and climate yield through Settings (API-10) |

Every change shows REQUESTED, then CONFIRMED or NOT CONFIRMED, with who made it and why. A line's
state is known while the controller is online; `irr_state` is published on change, so its age alone
never makes a line unknown. Before the engine ships, the cards show relay state and say that live job
state comes with the firmware. Run now on L01, Stop on any line and Stop all water work through the
bridge (§6.5); the other lines' Run, Clear fault and the bursts read "arrives with the irrigation
firmware".

## 10. Delivery

The work items are the VD issues in the [execution plan](execution-plan.md) (lanes L4, L5 and L7); its
§5 gives the order: the L01 bridge; the reads (minutes by zone work on today's relay edges); the engine
release (platform before the OTA, inert until `irr_program_id` appears); the clean lines, L01 first;
then the fertilizer lines, L02 with L03, then L08 and L10 (L05 and L06 stay reserved). PRs that touch
`verdify_schemas/**`, `ingestor/entity_map.py` or `mcp/server.py` name their restart (rule 7).
