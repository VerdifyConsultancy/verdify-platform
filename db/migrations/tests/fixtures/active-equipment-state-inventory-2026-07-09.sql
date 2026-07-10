-- Independent read-only inventory captured from the 97,033-row production-
-- shaped equipment_state replay used for issue #437 on 2026-07-09. The alias
-- contract intentionally covers physical control outputs, not firmware status,
-- derived-state, occupancy, or configuration/readback telemetry.

CREATE TEMP TABLE captured_equipment_state_inventory (
    equipment text PRIMARY KEY,
    telemetry_role text NOT NULL CHECK (
        telemetry_role IN ('physical_control_output', 'status_or_configuration')
    ),
    evidence_ref text NOT NULL
);

INSERT INTO captured_equipment_state_inventory (
    equipment, telemetry_role, evidence_ref
) VALUES
    ('drip_center', 'physical_control_output', 'resource437:equipment_state'),
    ('drip_center_fert', 'physical_control_output', 'resource437:equipment_state'),
    ('drip_wall', 'physical_control_output', 'resource437:equipment_state'),
    ('drip_wall_fert', 'physical_control_output', 'resource437:equipment_state'),
    ('fan1', 'physical_control_output', 'resource437:equipment_state'),
    ('fan2', 'physical_control_output', 'resource437:equipment_state'),
    ('fert_master_valve', 'physical_control_output', 'resource437:equipment_state'),
    ('fog', 'physical_control_output', 'resource437:equipment_state'),
    ('grow_light_grow', 'physical_control_output', 'resource437:equipment_state'),
    ('grow_light_main', 'physical_control_output', 'resource437:equipment_state'),
    ('heat1', 'physical_control_output', 'resource437:equipment_state'),
    ('heat2', 'physical_control_output', 'resource437:equipment_state'),
    ('mister_center', 'physical_control_output', 'resource437:equipment_state'),
    ('mister_south', 'physical_control_output', 'resource437:equipment_state'),
    ('mister_south_fert', 'physical_control_output', 'resource437:equipment_state'),
    ('mister_west', 'physical_control_output', 'resource437:equipment_state'),
    ('mister_west_fert', 'physical_control_output', 'resource437:equipment_state'),
    ('vent', 'physical_control_output', 'resource437:equipment_state'),
    ('economiser_blocked', 'status_or_configuration', 'resource437:equipment_state'),
    ('economiser_enabled', 'status_or_configuration', 'resource437:equipment_state'),
    ('fan_burst_active', 'status_or_configuration', 'resource437:equipment_state'),
    ('fog_burst_active', 'status_or_configuration', 'resource437:equipment_state'),
    ('fog_closes_vent', 'status_or_configuration', 'resource437:equipment_state'),
    ('gl_auto_mode', 'status_or_configuration', 'resource437:equipment_state'),
    ('heap_pressure_critical', 'status_or_configuration', 'resource437:equipment_state'),
    ('heap_pressure_warning', 'status_or_configuration', 'resource437:equipment_state'),
    ('irrigation_center_enabled', 'status_or_configuration', 'resource437:equipment_state'),
    ('irrigation_enabled', 'status_or_configuration', 'resource437:equipment_state'),
    ('irrigation_wall_enabled', 'status_or_configuration', 'resource437:equipment_state'),
    ('irrigation_weather_skip', 'status_or_configuration', 'resource437:equipment_state'),
    ('leak_detected', 'status_or_configuration', 'resource437:equipment_state'),
    ('mister_any', 'status_or_configuration', 'resource437:equipment_state'),
    ('mister_budget_exceeded', 'status_or_configuration', 'resource437:equipment_state'),
    ('occupancy', 'status_or_configuration', 'resource437:equipment_state'),
    ('occupancy_inhibit', 'status_or_configuration', 'resource437:equipment_state'),
    ('occupancy_quiet_override_active', 'status_or_configuration', 'resource437:equipment_state'),
    ('sntp_status', 'status_or_configuration', 'resource437:equipment_state'),
    ('vent_bypass_active', 'status_or_configuration', 'resource437:equipment_state'),
    ('water_flowing', 'status_or_configuration', 'resource437:equipment_state');
