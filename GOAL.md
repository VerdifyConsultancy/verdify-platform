Goal: Refactor greenhouse lighting control so occupancy becomes a lux-gated task-light demand, not an unconditional "lights on" override, and reduce occupancy-to-light latency from up to 60s to roughly 5-10s.

Context

Current behavior has three problems:

1. Lighting decisions only run every 60s, so even immediate Frigate occupancy detection can wait up to a minute before affecting lights.
2. Once inside the configured lighting window, occupancy currently forces lights on before lux and target-minute checks.
3. The control model is wrong: "occupied" should mean "allow task lighting when it is actually dark enough," not "turn lights on regardless of brightness."

Implement a new lighting contract with two independent demands:

    desired_on = plant_supplement_demand OR occupancy_task_light_demand

Plant supplement demand must preserve the existing grow-light behavior:

    auto_enabled
    AND time_valid
    AND inside configured plant light window
    AND target_light_minutes not met
    AND (
        lux below ON threshold
        OR current light is on and lux below OFF threshold
    )

Occupancy task-light demand must be independent of the plant lighting window:

    auto_enabled
    AND greenhouse_occupied
    AND exterior_lux is fresh
    AND (
        exterior_lux below ON threshold
        OR current light is on and exterior_lux below OFF threshold
    )

Important behavior changes

- Occupancy is gated by exterior lux, not by time of day.
- If it is noon and exterior lux is high, entering the greenhouse must not turn lights on.
- If it is midnight and exterior lux is low, entering the greenhouse may turn lights on even outside the configured plant light window.
- The existing overnight/off-period behavior for plant supplementation must remain intact because the plant path still respects the configured light window.
- When occupancy clears, lights should turn off unless plant supplementation independently wants them on.
- Occupancy hysteresis should prevent flicker: if occupancy turned the lights on because exterior lux was below the ON threshold, keep them on until exterior lux rises past the OFF threshold.
- If exterior lux is stale or missing, do not silently fall back to indoor lux for occupancy task lighting. Set occupancy task demand false and expose a reason such as `occupancy_lux_unavailable`.
- Indoor lux may remain a fallback or input for the existing plant automation, but occupancy task lighting should explicitly use fresh Tempest exterior lux.

Firmware latency change

In `firmware/greenhouse/controls.yaml`, change the lighting decision loop currently around line 1156 so lighting decisions evaluate every 5s, matching the main controller cadence.

Do not publish full telemetry every 5s. Preserve the existing 60s heartbeat behavior, but additionally publish telemetry/status when the lighting state or reason changes.

Target event path:

    Frigate detection
    -> MQTT
    -> ingestor
    -> ESPHome API push
    -> next 5s firmware lighting tick
    -> Home Assistant Lutron service

Expected result: lights should turn on within roughly 5-10s after Frigate detects occupancy, not up to 60s.

Firmware logic change

In `firmware/lib/greenhouse_logic.h` around line 250, replace the current priority model:

    auto disabled
    outside window
    occupied -> ON
    minutes met
    lux low
    hysteresis hold

with separate demand/reason handling for at least:

    auto_disabled
    occupancy_lux_low
    occupancy_hysteresis_hold
    occupancy_lux_unavailable
    plant_lux_low
    plant_hysteresis_hold
    minutes_met
    lux_sufficient
    outside_window
    min_on_hold
    min_off_hold

The decision should be explainable as:

    occupancy_task_light_demand = ...
    plant_supplement_demand = ...
    desired_on = occupancy_task_light_demand OR plant_supplement_demand

Min-on and min-off dwell behavior must still be respected.

Required behavior matrix

- Occupied + bright fresh exterior lux: lights OFF.
- Occupied + dark fresh exterior lux: lights ON, regardless of hour.
- Occupied + missing/stale exterior lux: occupancy demand OFF with reason `occupancy_lux_unavailable`.
- Empty + outside plant window: lights OFF.
- Empty + inside plant window + low lux + target minutes remaining: existing plant automation still turns lights ON.
- Occupied + lights already on + exterior lux rises slightly above ON threshold: keep ON until OFF threshold is crossed.
- Occupancy clears: lights OFF unless plant automation still wants them ON.
- Auto disabled: lights OFF / no automatic demand, preserving existing semantics.

Traceability and dashboard/status changes

Update the database/status traceability layer so live dashboards do not lie after the firmware change.

Update `v_lighting_traceability_now` and related lighting status views to expose:

    occupancy_active
    exterior_lux
    occupancy_lux_demand
    plant_supplement_demand
    expected_on
    firmware_reason
    actual_on

Where:

    expected_on = occupancy_lux_demand OR plant_supplement_demand

Forecast timelines should continue to show plant automation only, because future occupancy is not knowable.

Live status panels should show occupancy as a real-time override/demand lane.

Acceptance criteria

1. When greenhouse occupancy becomes active and fresh exterior lux is below the occupancy ON threshold, lights come on within roughly 5-10s.
2. When greenhouse occupancy becomes active during bright daylight, lights stay off.
3. At night, occupancy can turn task lights on even outside the plant light window.
4. If nobody is present overnight, lights stay off unless plant supplementation independently requires them.
5. Existing grow-light supplementation still works inside the configured window based on lux, hysteresis, target light minutes, and dwell rules.
6. When occupancy ends, lights turn off unless plant supplementation independently wants them on.
7. Dashboards/status views explain whether lights are expected on because of `occupancy_lux_low`, `occupancy_hysteresis_hold`, `plant_lux_low`, or `plant_hysteresis_hold`.
8. Missing or stale exterior lux does not cause occupancy to turn lights on.
9. Lighting control evaluates every 5s, while telemetry still publishes on change or on the existing 60s heartbeat.
10. Existing firmware safety invariants and dwell protections remain intact.

Validation plan

Add or update firmware tests for:

- occupied + low fresh exterior lux -> ON
- occupied + high fresh exterior lux -> OFF
- occupied + stale/missing exterior lux -> OFF with `occupancy_lux_unavailable`
- empty + outside plant window -> OFF
- occupied + outside plant window + low exterior lux -> ON
- empty + inside plant window + low lux + minutes remaining -> ON
- occupancy clears while plant demand false -> OFF
- occupancy clears while plant demand true -> remains ON
- occupancy hysteresis hold
- plant hysteresis hold
- min-off dwell still holding
- min-on dwell still holding

Then run:

    make test-firmware
    make firmware-invariants
    make lighting-audit-static

Also run a firmware replay diff and include the normal firmware PR artifacts before OTA, because this changes real relay behavior under occupancy.

Implementation notes

- Keep the change minimal and contract-focused.
- Preserve existing grow-light behavior except where it must be split into explicit plant demand.
- Do not make occupancy depend on the configured plant lighting window.
- Do not use indoor lux as a silent fallback for occupancy task lighting.
- Use Tempest exterior lux as the primary and explicit occupancy gate only when fresh.
- Ensure firmware reason strings and status/DB traceability names remain stable and machine-readable.
