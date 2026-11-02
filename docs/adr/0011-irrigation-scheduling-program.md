# ADR-0011: A compiled irrigation schedule on the controller for every wet line

- Status: accepted (Jason, 2026-11-01; Round 2, 2b and the Q-01 to Q-14 defaults applied)
- Date: 2026-11-01
- Owners: firmware-control and irrigation (platform) modules
- Requirement IDs: `NSR-003`, `NSR-004`, `NSR-007`
- Amends: [ADR-007](0007-irrigation-topology-and-commissioning.md)
- Design: [Irrigation program v2](../design/dashboard/irrigation.md)

## Context

Jason decided this himself on 2026-11-01. He wants every clean-water and fertilizer line online, with
schedules set from the dashboard, watering minutes by zone, and MCP tools for agents to adjust and
monitor schedules. Each mister should take part in VPD control, run on a schedule, or both. Later that
day he rejected the first design as "far too complicated with far too many holds, safety gates". This
is an experimental greenhouse, and forward progress comes first.

ADR-007 cannot carry this:

- It sends all climate demand to the reverse-exclusive center mister
  (`firmware/lib/irrigation_policy.h:66-79`) and hard-codes south and west off
  (`firmware/greenhouse/controls.yaml:1046-1047`).
- It keeps the center drip disabled and turns the fertilizer relays of L06, L08 and L10 off
  unconditionally (`firmware/greenhouse/hardware.yaml:443-446, 465-468, 552-555`).
- It limits fertilizer to a weekly wall feed behind nine evidence bits, which was never commissioned.

The wall drip has not run since 2026-05-29: its only trigger is a Home Assistant button the platform
cannot press (`firmware/greenhouse/tunables.yaml:2410-2425`), and nothing publishes job state. The
ingestor, the only device writer, was halted from 2026-10-19 to 2026-11-01 by the #433 bounded-reconcile
gate. The July heap collapse (#428, #430) rules out per-field schedule entities.

## Decision

1. **Hybrid authority.** The controller stores a compiled schedule in NVS and runs it offline. The
   record holds at most 24 slots, plus line enables, mister flags, caps (the three site values
   included) and pauses. The platform authors it in irrigation tables and compiles it
   deterministically. The ingestor pushes it through staged ESPHome services under the writer's fences
   whenever the controller's published hash differs. Run, stop, clear-fault and override are commands
   on the same path. Nothing uses `setpoint_changes`.
2. **Every connected line is schedulable.** Schedules exist for L01, L02 and L04-L10. Each sets the
   weekdays, a start (a clock time, or sunrise, solar noon or sunset ± an offset), `on_s`, pulses for
   misters, and, for fertilizer lines, a prewet and flush on the clean partner. L03, the injector line
   from the fertilizer tank, opens with any fertilizer run and is never scheduled on its own. Fog (L11)
   stays climate-only. The firmware hard-block on L06, L08 and L10 becomes a per-line enable. A line is
   enabled once a person has checked it and ticked Checked. L05 and L06 (center drips) are reserved:
   schedulable, with no bring-up now.
3. **Mister flags.** L04, L07 and L09 each have two independent flags, `vpd_member` and
   `schedule_member`, and all four combinations are allowed. The seed is today's behaviour: center VPD
   only. With more than one VPD member, the existing rank arbiter chooses, with center at rank 1. A
   zone whose probe is out uses its peer probes, then the house average.
4. **Existing caps, shared.** VPD and scheduled pulses alternate on one mist manifold, and in a VPD
   emergency VPD goes first. Both draw on the SAF-4 ceiling (360 gal), the soft budget (which only VPD
   pulses may bypass, and only in a VPD emergency), the zone duty caps (120, 45 and 45 min) and the
   re-fire fence. A VPD reserve per zone (60, 15 and 15 min) is closed to schedules. Drips have daily
   caps (wall 60, center 30 min). Counters survive soft resets and are zeroed on power-on.
5. **Doctrine.** Clean water fails open within the caps. Fertilizer fails closed only on its own
   state: a disabled line, an unreadable program, or an interrupted run, which is never resumed and is
   followed by one clean flush. No climate or soil sensor gates a drip, clean or fertilizer. SAFETY_COOL
   survival fog preempts clean drips, and neither drips, the SAF-4 ceiling nor the soft budget veto it.
   A drip holds climate wetting for at most 15 minutes. The leak signal, which also counts the hose,
   sink and hydro, only alerts (Q-05): the engine drops it from every fog, mister and drip force-off
   and the irrigation cancel; until then today's interlock stays.
6. **Occupancy and time.** Mist and fog hold only while Frigate confirms occupancy through the Home
   Assistant sensor, the only input; unknown occupancy means mist runs. Time comes from NTP only, with
   no RTC and no STOP button. Without valid time nothing starts on schedule, while run-now and stop
   still work.
7. **Evidence.** The controller publishes `irr_program_id` (the program hash), `irr_state` (runs,
   reasons, faults and VPD sources, on change and before a relay opens) and `irr_events` (recent
   outcomes with sequence numbers). Minutes by zone come from migration 194's relay-edge runs, with cause
   and outcome from the engine; whole gallons from the calibrated meter are informational, with no flow
   flag. Every occurrence ends in exactly one outcome.
8. **Authority.** Operators, and every MCP agent including Iris, write through
   `verdify_services.irrigation`, within the bounds in `verdify_schemas/irrigation.py`. The last write
   wins; dashboard edits default to manual, agent edits do not, and only Iris is refused on a manual
   item. Only a person records a line's check. Nothing is refused while an experiment is armed; changes
   are logged with its id (Round 2). No envelopes, holds, observe modes, step-up or review dates.
9. **Home Assistant** keeps occupancy data, Lutron switch control and, until a direct reader replaces
   it, the hydroponic probe read. Its buttons, mirrors, MQTT topics, redundant Tempest read, recorder
   backfill and irrigation dashboard go. The buttons go in the engine release, which adds run-now and
   `manual_override`; until then the ingestor presses the wall-drip button itself, so L01 runs first.

How this amends ADR-007:

| ADR-007 clause | Now |
|---|---|
| Climate demand targets center only; the center mister is reverse-exclusive | Per-zone `vpd_member` and `schedule_member` |
| South/west clean irrigation needs explicit disabled-by-default intent | A per-line enable after a recorded physical check |
| Center drip and all non-wall fertilizer remain represented but disabled | Every connected line is schedulable, fertilizer included |
| Commissioned weekly wall feed (nine bits, valid-until epochs, solar window, 7-day floor) | Fertilizer schedules with prewet and flush |
| One firmware owner of wet relays; relay guards; never-resume fertilizer; SAF-5; one outcome per run | Kept |

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Keep ADR-007 | A center schedule, and south or west VPD membership, are impossible by construction |
| A platform scheduler issuing timed run commands | Every drop of scheduled water would depend on the ingestor, the database and WiFi, which have failed for days at a time |
| The v1 program: a 72-hour horizon, commissioning states, envelopes, observe modes, review dates | Far too complicated; Jason rejected it |
| Per-field or per-slot entities with `cfg_*` readbacks | Hundreds of entities would repeat the July heap collapse |
| Compile-time schedules in firmware YAML | Every edit would need an OTA |
| Home Assistant automations | A hidden second actuator path, and Home Assistant is being cut back |

## Consequences

- One firmware release ships through `make firmware-deploy` when it is ready: the engine, services,
  readbacks, per-line enables, membership and peer-probe changes and the D-IRP-19 watchdog limit, less
  the buttons and about 70 retired entities. After it, every schedule, flag, cap and pause is data.
- The Python compiler and the C++ decoder must agree; one golden-vector file feeds both test suites.
- Tests, invariants and docs pinned to center-only misting change in the same release. So does
  `irrigation_fence_breach`, which now fires when a relay is on while its line is disabled.
- Agent and weather adjustments stop while the platform is down; the last program keeps running.
- Fertilizer carries real risk (no evidence bits or bench test; runs with sensors offline), the accepted
  trade here. SAF-5, never-resume, the flush and the per-line enable protect it.

## Validation and revisit trigger

Validation: the engine header passes its native tests on the golden vectors; L01 runs from the
dashboard before the OTA; `irr_program_id` confirms the first push; center VPD misting continues; a
scheduled L01 run, a center mist window and a fertilizer run with its flush appear in the ledger with
the right cause and outcome; and no relay turns on while its line is disabled.

Revisit if free heap falls below the pre-OTA floor (32 kB, `firmware/lib/heap_diagnostics.h`), if a
sub-meter, pressure sensing or a main shutoff is installed, if working soil probes make soil-moisture
gating worthwhile, or if NTP-only time loses scheduled runs during outages.
