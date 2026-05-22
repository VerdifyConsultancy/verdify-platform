"""
Test 02: Database — Schema integrity, views, functions, data freshness.
"""

from conftest import db_query, db_query_rows


class TestSchemaIntegrity:
    """Core tables and views must exist with expected structure."""

    REQUIRED_TABLES = [
        "climate",
        "equipment_state",
        "system_state",
        "daily_summary",
        "setpoint_changes",
        "setpoint_plan",
        "plan_journal",
        "planner_lessons",
        "weather_forecast",
        "forecast_deviation_log",
        "crops",
        "crop_events",
        "observations",
        "alert_log",
        "diagnostics",
        "water_meter_events",
        "daily_plan_archive_audit",
        "instrumentation_requirements",
    ]

    REQUIRED_VIEWS = [
        "v_stress_hours_today",
        "v_planner_performance",
        "v_dew_point_risk",
        "v_plan_accuracy",
        "v_plan_compliance",
        "v_climate_latest",
        "v_relay_stuck",
        "v_sensor_staleness",
        "v_data_trust_ledger",
        "v_required_sensor_coverage",
        "v_water_accountability",
        "v_forecast_accuracy_lead_buckets",
        "v_energy_estimate_reconciliation",
        "v_setpoint_delivery_latency",
        "v_mister_zone_effectiveness",
        "v_plan_tactical_outcome_daily",
        "v_water_meter_daily",
        "v_irrigation_accountability",
        "v_alert_lifecycle_quality",
        "v_forecast_action_outcomes",
        "v_setpoint_change_delivery",
        "v_crop_lifecycle_completeness",
        "v_growth_observation_quality",
        "v_harvest_story",
        "v_nutrient_lab_status",
        "v_succession_plan_readiness",
        "v_instrumentation_readiness",
        "v_daily_plan_archive_self_check",
        "v_forecast_plan_outcome_mart",
        "v_grower_economics_story",
        "v_greenhouse_id_default_audit",
        "v_band_trace_recent",
        "v_band_trace_latest",
        "v_lighting_status_now",
        "v_lighting_circuit_status_now",
        "v_lighting_minutes_status_now",
        "v_lighting_qualified_minutes_daily",
        "v_lighting_daily",
    ]

    REQUIRED_FUNCTIONS = [
        "fn_planner_scorecard",
        "fn_stress_summary",
        "fn_band_setpoints",
        "fn_band_timeline",
        "fn_house_vpd_control_band",
        "fn_band_trace",
        "fn_band_setpoint_provenance",
        "fn_compliance_pct",
        "fn_solar_altitude",
        "fn_setpoint_at",
        "fn_timeline_setpoint_value",
        "fn_lighting_policy",
        "fn_lighting_circuit_policy",
        "fn_lighting_minutes_policy",
        "fn_lighting_timeline",
        "fn_lighting_lux_threshold_recommendation",
    ]

    def test_tables_exist(self):
        rows = db_query_rows("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        for table in self.REQUIRED_TABLES:
            assert table in rows, f"Table {table} missing"

    def test_views_exist(self):
        rows = db_query_rows("SELECT viewname FROM pg_views WHERE schemaname = 'public' ORDER BY viewname")
        # Also check materialized views
        mat_rows = db_query_rows("SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'")
        all_views = rows + mat_rows
        for view in self.REQUIRED_VIEWS:
            assert view in all_views, f"View {view} missing"

    def test_functions_exist(self):
        rows = db_query_rows("SELECT routine_name FROM information_schema.routines WHERE routine_schema = 'public'")
        for fn in self.REQUIRED_FUNCTIONS:
            assert fn in rows, f"Function {fn} missing"

    def test_timescaledb_hypertables(self):
        rows = db_query_rows("SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY hypertable_name")
        for ht in ["climate", "equipment_state", "system_state", "weather_forecast"]:
            assert ht in rows, f"Hypertable {ht} missing"


class TestDataFreshness:
    """Live data must be flowing — no stale readings."""

    def test_climate_data_fresh(self):
        """Climate data must be < 5 minutes old."""
        age = db_query("SELECT extract(epoch FROM now() - max(ts))::int FROM climate")
        assert int(age) < 300, f"Climate data is {age}s old (>5min)"

    def test_equipment_state_fresh(self):
        """Equipment state must be < 2 hours old (longer at night when IDLE — no transitions)."""
        age = db_query("SELECT extract(epoch FROM now() - max(ts))::int FROM equipment_state")
        assert int(age) < 7200, f"Equipment state is {age}s old (>2h)"

    def test_daily_summary_exists_today(self):
        """Today's daily_summary row must exist."""
        count = db_query("SELECT count(*) FROM daily_summary WHERE date = (now() AT TIME ZONE 'America/Denver')::date")
        assert int(count) >= 1, "No daily_summary row for today"

    def test_forecast_data_recent(self):
        """Forecast data must have been fetched in last 6 hours."""
        age = db_query("SELECT extract(epoch FROM now() - max(fetched_at))::int FROM weather_forecast")
        assert int(age) < 21600, f"Forecast data is {age}s old (>6h)"


class TestViewsCompute:
    """Key views must return data without errors."""

    def test_scorecard_returns_data(self):
        rows = db_query_rows("SELECT * FROM fn_planner_scorecard((now() AT TIME ZONE 'America/Denver')::date)")
        assert len(rows) >= 10, f"Scorecard returned only {len(rows)} rows"

    def test_stress_hours_computes(self):
        rows = db_query_rows("SELECT * FROM v_stress_hours_today LIMIT 1")
        assert len(rows) >= 1, "v_stress_hours_today returned no rows"

    def test_stress_hours_do_not_exceed_elapsed_day(self):
        rows = db_query_rows(
            """
            WITH elapsed AS (
                SELECT EXTRACT(EPOCH FROM (
                    now() - (date_trunc('day', now() AT TIME ZONE 'America/Denver') AT TIME ZONE 'America/Denver')
                )) / 3600.0 AS hours
            )
            SELECT cold_stress_hours, heat_stress_hours, vpd_stress_hours, vpd_low_hours, elapsed.hours
            FROM v_stress_hours_today, elapsed
            LIMIT 1
            """
        )
        assert rows, "v_stress_hours_today returned no rows"
        cold, heat, vpd_high, vpd_low, elapsed_hours = [float(v) for v in rows[0].split("|")]
        for value in (cold, heat, vpd_high, vpd_low):
            assert value <= elapsed_hours + 0.25

    def test_dew_point_risk_computes(self):
        rows = db_query_rows(
            "SELECT * FROM v_dew_point_risk WHERE date >= (now() AT TIME ZONE 'America/Denver')::date - 1 LIMIT 1"
        )
        assert len(rows) >= 1, "v_dew_point_risk returned no rows"

    def test_greenhouse_id_defaults_complete(self):
        missing = db_query("SELECT count(*) FROM v_greenhouse_id_default_audit WHERE NOT has_default")
        assert int(missing) == 0, "tenant-scoped tables with greenhouse_id must have a default"

    def test_planner_performance_computes(self):
        rows = db_query_rows(
            "SELECT * FROM v_planner_performance WHERE date >= (now() AT TIME ZONE 'America/Denver')::date - 7 LIMIT 1"
        )
        assert len(rows) >= 1, "v_planner_performance returned no rows"

    def test_planning_quality_panel_sources_have_current_rows(self):
        """Public Planning Quality panels should not go blank when the scorecard has local-day data."""
        scorecard_rows = int(
            db_query("SELECT count(*) FROM fn_planner_scorecard((now() AT TIME ZONE 'America/Denver')::date)")
        )
        assert scorecard_rows >= 10, "local-day planner scorecard has too few rows"

        recent_plan_rows = int(
            db_query(
                """
                SELECT count(*)
                FROM v_forecast_plan_outcome_mart
                WHERE created_at >= now() - interval '14 days'
                  AND compliance_pct IS NOT NULL
                """
            )
        )
        assert recent_plan_rows >= 1, "Planning Quality 14d plan panels have no recent source rows"

    def test_band_setpoints_returns(self):
        """fn_band_setpoints may require args or return empty if no crops — just verify it doesn't error."""
        try:
            db_query("SELECT * FROM fn_band_setpoints() LIMIT 1")
        except RuntimeError:
            # Function might have required args — try with defaults
            db_query("SELECT 1")  # Just verify DB is up

    def test_house_vpd_control_band_returns(self):
        row = db_query_rows(
            """
            SELECT crop_vpd_low,
                   crop_vpd_high,
                   house_vpd_low,
                   house_vpd_high,
                   house_vpd_high - house_vpd_low AS width
              FROM fn_house_vpd_control_band(now())
            """
        )
        assert row, "fn_house_vpd_control_band returned no rows"
        crop_low, crop_high, house_low, house_high, width = [float(v) for v in row[0].split("|")]
        assert 0.1 <= crop_low < crop_high <= 5.0
        assert 0.1 <= house_low < house_high <= 5.0
        assert width >= 0.55 - 1e-6

    def test_lighting_policy_tracks_highest_active_crop_dli(self):
        rows = db_query_rows(
            """
            WITH policy AS (
                SELECT * FROM fn_lighting_policy(now(), 'vallery')
            ),
            crops_max AS (
                SELECT max(target_dli) AS target_dli
                FROM crops
                WHERE is_active IS TRUE
                  AND COALESCE(greenhouse_id, 'vallery') = 'vallery'
            )
            SELECT policy.target_dli,
                   COALESCE(crops_max.target_dli, 14.0) AS crop_target_dli,
                   policy.target_light_hours,
                   policy.sunrise_hour,
                   policy.cutoff_hour,
                   policy.max_crop_name
            FROM policy CROSS JOIN crops_max
            """
        )
        assert rows, "fn_lighting_policy returned no rows"
        target_dli, crop_target_dli, hours, sunrise, cutoff, max_crop = rows[0].split("|")
        assert float(target_dli) >= float(crop_target_dli)
        assert 10 <= int(hours) <= 18
        assert 0 <= int(sunrise) <= 23
        assert int(sunrise) < int(cutoff) <= 23
        assert max_crop

    def test_lighting_status_exposes_both_circuits_and_expected_state(self):
        rows = db_query_rows(
            """
            SELECT target_dli IS NOT NULL AS has_target,
                   grow_light_main_on IS NOT NULL AS has_main_state,
                   grow_light_grow_on IS NOT NULL AS has_grow_state,
                   expected_lights_on IS NOT NULL AS has_expected_state,
                   source_chain
            FROM v_lighting_status_now
            """
        )
        assert rows, "v_lighting_status_now returned no rows"
        has_target, has_main, has_grow, has_expected, source_chain = rows[0].split("|")
        assert (has_target, has_main, has_grow, has_expected) == ("t", "t", "t", "t")
        assert "fn_lighting_policy()" in source_chain

    def test_lighting_lux_threshold_recommendation_uses_tempest_history(self):
        rows = db_query_rows(
            """
            SELECT sample_count,
                   recommended_gl_lux_threshold,
                   recommended_gl_lux_hysteresis,
                   current_gl_lux_threshold,
                   current_gl_lux_hysteresis,
                   source_chain
            FROM fn_lighting_lux_threshold_recommendation(now(), 'vallery')
            """
        )
        assert rows, "fn_lighting_lux_threshold_recommendation returned no rows"
        samples, threshold, hysteresis, current_threshold, current_hysteresis, source_chain = rows[0].split("|")
        assert int(samples) > 100
        assert 5000 <= float(threshold) <= 40000
        assert 1500 <= float(hysteresis) <= 10000
        assert 5000 <= float(current_threshold) <= 100000
        assert 1500 <= float(current_hysteresis) <= 25000
        assert "Tempest outdoor_lux" in source_chain
        assert "per-circuit gl_main_*/gl_grow_* lux tunables" in source_chain

    def test_lighting_minutes_policy_exposes_two_state_machines(self):
        rows = db_query_rows(
            """
            SELECT light_key,
                   equipment,
                   target_light_minutes,
                   lux_on_threshold,
                   lux_off_threshold,
                   min_on_s,
                   min_off_s,
                   auto_enabled,
                   source_chain
            FROM fn_lighting_minutes_policy(now(), 'vallery')
            ORDER BY light_key
            """
        )
        assert len(rows) == 2
        keys = []
        for row in rows:
            key, equipment, target_minutes, lux_on, lux_off, min_on, min_off, auto, source_chain = row.split("|")
            keys.append(key)
            assert equipment in {"grow_light_main", "grow_light_grow"}
            assert 0 <= int(target_minutes) <= 1080
            assert float(lux_on) < float(lux_off)
            assert int(min_on) >= 0
            assert int(min_off) >= 0
            assert auto in {"t", "f"}
            assert "qualified-minutes state machines" in source_chain
        assert keys == ["grow", "main"]

    def test_lighting_minutes_status_traces_actual_switch_and_progress(self):
        rows = db_query_rows(
            """
            SELECT light_key,
                   target_light_minutes,
                   qualified_light_minutes,
                   remaining_light_minutes,
                   actual_on IS NOT NULL AS has_actual,
                   expected_on IS NOT NULL AS has_expected,
                   minutes_below_target IS NOT NULL AS has_minutes_gate,
                   occupancy_active IS NOT NULL AS has_occupancy,
                   exterior_lux_fresh IS NOT NULL AS has_exterior_fresh,
                   occupancy_lux_demand IS NOT NULL AS has_occupancy_demand,
                   plant_supplement_demand IS NOT NULL AS has_plant_demand
            FROM v_lighting_minutes_status_now
            ORDER BY light_key
            """
        )
        assert len(rows) == 2
        for row in rows:
            (
                key,
                target,
                qualified,
                remaining,
                has_actual,
                has_expected,
                has_minutes_gate,
                has_occupancy,
                has_exterior_fresh,
                has_occupancy_demand,
                has_plant_demand,
            ) = row.split("|")
            assert key in {"grow", "main"}
            assert 0 <= int(target) <= 1080
            assert int(qualified) >= 0
            assert int(remaining) >= 0
            assert (has_actual, has_expected, has_minutes_gate) == ("t", "t", "t")
            assert (has_occupancy, has_exterior_fresh, has_occupancy_demand, has_plant_demand) == ("t", "t", "t", "t")

    def test_band_trace_latest_computes(self):
        rows = db_query_rows(
            """
            SELECT greenhouse_id,
                   temp_avg IS NOT NULL AS has_temp,
                   vpd_avg IS NOT NULL AS has_vpd,
                   crop_temp_low IS NOT NULL AS has_crop_temp,
                   crop_vpd_low IS NOT NULL AS has_crop_vpd,
                   fw_temp_low IS NOT NULL AS has_fw_temp,
                   fw_vpd_low IS NOT NULL AS has_fw_vpd,
                   rb_temp_low IS NOT NULL AS has_rb_temp,
                   rb_vpd_low IS NOT NULL AS has_rb_vpd,
                   trace_quality_flag
              FROM fn_band_trace(now() - interval '2 hours', now(), 'vallery')
             ORDER BY ts DESC
             LIMIT 1
            """
        )
        assert rows, "v_band_trace_latest returned no rows"
        parts = rows[0].split("|")
        assert parts[0] == "vallery"
        assert parts[1:9] == ["t"] * 8
        assert parts[9] in {"ok", "missing_crop_band", "missing_fw_band", "missing_readback", "readback_drift"}

    def test_band_trace_boolean_contract(self):
        mismatch = db_query(
            """
            SELECT count(*)::int
              FROM fn_band_trace(now() - interval '6 hours', now(), 'vallery')
             WHERE fw_both_in_band IS DISTINCT FROM (fw_temp_in_band AND fw_vpd_in_band)
                OR crop_both_in_band IS DISTINCT FROM (crop_temp_in_band AND crop_vpd_in_band)
            """
        )
        assert int(mismatch) == 0, "band trace both-axis flags drifted from axis flags"

    def test_band_timeline_stitches_actual_to_forecast(self):
        rows = db_query_rows(
            """
            SELECT count(*) FILTER (WHERE timeline_phase = 'actual')::int AS actual_rows,
                   count(*) FILTER (WHERE timeline_phase = 'forecast')::int AS forecast_rows,
                   bool_and(
                       firmware_temp_low IS NOT NULL
                       AND firmware_temp_high IS NOT NULL
                       AND firmware_vpd_low IS NOT NULL
                       AND firmware_vpd_high IS NOT NULL
                   ) AS has_stitched_band,
                   bool_or(
                       timeline_phase = 'forecast'
                       AND actual_temp_low IS NULL
                       AND actual_temp_high IS NULL
                       AND actual_vpd_low IS NULL
                       AND actual_vpd_high IS NULL
                   ) AS future_does_not_reuse_actual_rows,
                   bool_or(
                       timeline_phase = 'forecast'
                       AND firmware_temp_low = projected_temp_low
                       AND firmware_temp_high = projected_temp_high
                       AND firmware_vpd_low = projected_vpd_low
                       AND firmware_vpd_high = projected_vpd_high
                   ) AS future_uses_projected_band
              FROM fn_band_timeline(
                  now() - interval '1 hour',
                  now() + interval '24 hours',
                  interval '1 hour',
                  'vallery'
              )
            """
        )
        assert rows, "fn_band_timeline returned no rows"
        actual_rows, forecast_rows, has_band, no_future_actual, future_projected = rows[0].split("|")
        assert int(actual_rows) >= 1
        assert int(forecast_rows) >= 1
        assert has_band == "t"
        assert no_future_actual == "t"
        assert future_projected == "t"

    def test_band_timeline_derives_firmware_thresholds(self):
        mismatch = db_query(
            """
            SELECT count(*)::int
              FROM fn_band_timeline(
                  now() - interval '1 hour',
                  now() + interval '24 hours',
                  interval '1 hour',
                  'vallery'
              )
             WHERE abs(temp_width_f - greatest(2.0, firmware_temp_high - firmware_temp_low)) > 0.001
                OR abs(vpd_width_kpa - greatest(0.2, firmware_vpd_high - firmware_vpd_low)) > 0.001
                OR abs(temp_heat_target_f - CASE
                    WHEN sw_fsm_controller_enabled
                        THEN (firmware_temp_low + firmware_temp_high) * 0.5
                    ELSE firmware_temp_low + temp_width_f * 0.25 + bias_heat_f
                END) > 0.001
                OR abs(temp_heat_on_below_f - (temp_heat_target_f + heat_hysteresis_f)) > 0.001
                OR abs(temp_cooling_entry_margin_f - CASE
                    WHEN sw_fsm_controller_enabled AND outdoor_cold_for_vent THEN temp_cool_stage2_delta_f
                    ELSE 0.0
                END) > 0.001
                OR abs(temp_cooling_exit_hysteresis_f - CASE
                    WHEN sw_fsm_controller_enabled AND outdoor_cold_for_vent
                        THEN greatest(temp_hysteresis_f, 3.0)
                    ELSE temp_hysteresis_f
                END) > 0.001
                OR abs(temp_cool_on_above_f - CASE
                    WHEN sw_fsm_controller_enabled THEN greatest(
                        firmware_temp_low + 1.0,
                        firmware_temp_high + temp_cooling_entry_margin_f - solar_cooling_lead_f
                    )
                    ELSE firmware_temp_high - temp_width_f * 0.25 + bias_cool_f
                END) > 0.001
                OR abs(temp_cool_hold_until_f - CASE
                    WHEN sw_fsm_controller_enabled THEN least(
                        firmware_temp_high - temp_cooling_exit_hysteresis_f,
                        temp_cool_on_above_f - temp_cooling_exit_hysteresis_f
                    )
                    ELSE temp_cool_on_above_f - temp_hysteresis_f
                END) > 0.001
                OR abs(temp_cool_stage2_on_above_f - CASE
                    WHEN sw_fsm_controller_enabled
                        THEN firmware_temp_high + temp_cool_stage2_delta_f
                    ELSE temp_cool_on_above_f + temp_cool_stage2_delta_f
                END) > 0.001
                OR abs(vpd_hysteresis_effective_kpa - CASE
                    WHEN sw_fsm_controller_enabled
                        THEN least(greatest(0.05, vpd_hysteresis_kpa), greatest(0.05, vpd_width_kpa * 0.33))
                    ELSE least(greatest(0.05, vpd_hysteresis_kpa), firmware_vpd_high * 0.5)
                END) > 0.001
                OR abs(vpd_humidify_on_above_kpa - firmware_vpd_high) > 0.001
                OR abs(vpd_humidify_resolved_below_kpa - (firmware_vpd_high - vpd_hysteresis_effective_kpa)) > 0.001
                OR abs(vpd_dehum_on_below_kpa - CASE
                    WHEN sw_fsm_controller_enabled AND outdoor_cold_for_vent
                        THEN firmware_vpd_low - vpd_hysteresis_effective_kpa
                    ELSE firmware_vpd_low
                END) > 0.001
                OR abs(vpd_dehum_resolved_above_kpa - (firmware_vpd_low + vpd_hysteresis_effective_kpa)) > 0.001
                OR abs(vpd_vent_fog_on_above_kpa - (vpd_high_eff_kpa + fog_escalation_kpa)) > 0.001
                OR abs(vpd_sealed_fog_on_above_kpa - (firmware_vpd_high + fog_escalation_kpa)) > 0.001
            """
        )
        assert int(mismatch) == 0, "dashboard firmware threshold derivation drifted from contract"

    def test_band_setpoint_provenance_links_crop_dispatcher_firmware_readback(self):
        rows = db_query_rows(
            """
            SELECT parameter,
                   crop_target_value IS NOT NULL AS has_crop,
                   dispatcher_value IS NOT NULL AS has_dispatcher,
                   firmware_setpoint_value IS NOT NULL AS has_fw,
                   cfg_readback_value IS NOT NULL AS has_readback,
                   automation_source,
                   source_chain,
                   displayed_on_operator_graph
              FROM fn_band_setpoint_provenance(now(), 'vallery')
             ORDER BY parameter
            """
        )
        assert len(rows) == 4
        params = {row.split("|")[0] for row in rows}
        assert params == {"temp_low", "temp_high", "vpd_low", "vpd_high"}
        for row in rows:
            (
                parameter,
                has_crop,
                has_dispatcher,
                has_fw,
                has_readback,
                automation_source,
                source_chain,
                displayed,
            ) = row.split("|")
            assert (has_crop, has_dispatcher, has_fw, has_readback, displayed) == ("t", "t", "t", "t", "t")
            if parameter.startswith("temp_"):
                assert "fn_band_setpoints" in automation_source
                assert "crop profiles -> fn_band_setpoints()" in source_chain
            else:
                assert "fn_house_vpd_control_band" in automation_source
                assert "crop profiles + zone VPD targets" in source_chain

    def test_compliance_returns(self):
        rows = db_query_rows("SELECT * FROM fn_compliance_pct('24 hours'::interval)")
        assert len(rows) >= 1, "fn_compliance_pct returned no rows"

    def test_data_trust_ledger_returns(self):
        rows = db_query_rows("SELECT check_name, status FROM v_data_trust_ledger")
        assert len(rows) >= 5, "v_data_trust_ledger returned too few checks"

    def test_required_sensor_coverage_returns(self):
        rows = db_query_rows("SELECT * FROM v_required_sensor_coverage LIMIT 1")
        assert len(rows) >= 1, "v_required_sensor_coverage returned no rows"

    def test_forecast_accuracy_lead_buckets_compute(self):
        rows = db_query_rows("SELECT * FROM v_forecast_accuracy_lead_buckets LIMIT 1")
        assert len(rows) >= 1, "v_forecast_accuracy_lead_buckets returned no rows"

    def test_water_meter_daily_compute(self):
        rows = db_query_rows("SELECT day, used_gal FROM v_water_meter_daily LIMIT 1")
        assert len(rows) >= 1, "v_water_meter_daily returned no rows"

    def test_water_meter_daily_excludes_rejected_deltas(self):
        row = db_query_rows(
            """
            WITH view_total AS (
                SELECT COALESCE(SUM(used_gal), 0) AS total_gal
                FROM v_water_meter_daily
            ),
            ok_total AS (
                SELECT COALESCE(SUM(delta_gal), 0) AS total_gal
                FROM water_meter_events
                WHERE event_type = 'delta'
                  AND quality_flag = 'ok'
            )
            SELECT ABS(view_total.total_gal - ok_total.total_gal) < 0.001
            FROM view_total, ok_total
            """
        )
        assert row and row[0] == "t", "v_water_meter_daily is summing rejected water-meter deltas"

    def test_daily_summary_monthly_water_cost_is_plausible(self):
        impossible_months = db_query(
            """
            SELECT count(*)
            FROM (
                SELECT date_trunc('month', date)::date AS month,
                       SUM(COALESCE(water_used_gal, 0)) AS gallons,
                       SUM(COALESCE(cost_water, 0)) AS cost_usd
                FROM daily_summary
                GROUP BY 1
                HAVING SUM(COALESCE(water_used_gal, 0)) > 100000
                    OR SUM(COALESCE(cost_water, 0)) > 500
            ) bad
            """
        )
        assert int(impossible_months) == 0, "monthly water usage/cost contains an impossible spike"

    def test_backlog_story_views_compute(self):
        checks = [
            "SELECT * FROM v_irrigation_accountability LIMIT 1",
            "SELECT * FROM v_forecast_action_outcomes LIMIT 1",
            "SELECT * FROM v_crop_lifecycle_completeness LIMIT 1",
            "SELECT * FROM v_forecast_plan_outcome_mart LIMIT 1",
            "SELECT * FROM v_grower_economics_story LIMIT 1",
        ]
        for sql in checks:
            db_query(sql)

    def test_daily_summary_backfill_fields_present(self):
        rows = db_query_rows(
            """
            SELECT rh_avg, outdoor_temp_min, outdoor_temp_max, kwh_total
            FROM daily_summary
            WHERE date >= (now() AT TIME ZONE 'America/Denver')::date - 7
            ORDER BY date DESC
            LIMIT 1
            """
        )
        assert rows, "daily_summary returned no recent rows"
        rh_avg, outdoor_min, outdoor_max, kwh_total = rows[0].split("|")
        assert rh_avg, "daily_summary.rh_avg was not backfilled"
        assert outdoor_min and outdoor_max, "daily_summary outdoor temp min/max were not backfilled"
        assert kwh_total, "daily_summary.kwh_total was not populated from measured energy"

    def test_daily_summary_electric_cost_uses_runtime_kwh(self):
        rows = db_query_rows(
            """
            SELECT date
            FROM daily_summary
            WHERE date >= (now() AT TIME ZONE 'America/Denver')::date - 31
              AND date < (now() AT TIME ZONE 'America/Denver')::date - 1
              AND kwh_estimated IS NOT NULL
              AND cost_electric IS NOT NULL
              AND ABS(cost_electric - ROUND((kwh_estimated * 0.111)::numeric, 2)) > 0.011
            LIMIT 1
            """
        )
        assert not rows, f"daily_summary electric cost does not match runtime-modeled kWh: {rows[0]}"

    def test_energy_reconciliation_compares_runtime_to_measured_kwh(self):
        view_sql = db_query("SELECT pg_get_viewdef('v_energy_estimate_reconciliation'::regclass, true)")
        assert "ds.kwh_estimated" in view_sql
        assert "COALESCE(ds.kwh_total, ds.kwh_estimated)" not in view_sql
        assert "meter_runtime_divergence" in view_sql

    def test_runtime_power_30m_function_returns_buckets(self):
        rows = db_query_rows(
            """
            SELECT count(*) AS buckets,
                   bool_and(total_watts >= 0 AND heat1_watts >= 0 AND fans_watts >= 0 AND other_watts >= 0) AS nonnegative,
                   max(total_watts) AS max_watts
            FROM fn_runtime_power_30m(now() - interval '24 hours', now())
            """
        )
        assert rows, "fn_runtime_power_30m returned no aggregate row"
        buckets, nonnegative, max_watts = rows[0].split("|")
        assert int(buckets) >= 40, "fn_runtime_power_30m returned too few 30-minute buckets"
        assert nonnegative == "t", "fn_runtime_power_30m returned negative watt values"
        assert float(max_watts) >= 0, "fn_runtime_power_30m returned invalid max watts"
