-- #643: six distinct bounded runtime duties; immutable authentic267 predecessor.
DO $preflight$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=267
   AND sha256='39be9b39cf782b8cf815078d50f8fa8cd838f017584e91e5dbcb50669b8a8735' AND stamp_method='runner')
   OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=268)
   OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex')<>'fe79f986d58ba6deec513312441b5ba5d579168d3e7e28d5721bb5771150af81'
   OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex')<>'15e4eff5d86ff58bf3fc98075dfc4613b5fd2a418bf3be9251fd7e6b1634a96e'
   OR encode(public.fn_mcp_runtime_boundary_digest(),'hex')<>'81836c70a76578da82b77899da5d1cafee4597ea819e35ee68a3fd6cf669fa45'
   OR EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts r
      WHERE r.boundary_sha256 IS DISTINCT FROM public.fn_runtime_ordinary_boundary_digest(r.login_name))
   OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt r
      WHERE r.boundary_sha256=public.fn_mcp_runtime_boundary_digest())
 THEN RAISE EXCEPTION '#643 requires authentic unchanged267 predecessor'; END IF;
END $preflight$;
CREATE TEMP TABLE role643_predecessor_seals ON COMMIT DROP AS
SELECT login_name, boundary_sha256 FROM public.runtime_ordinary_login_attestation_receipts
UNION ALL SELECT 'verdify_mcp_runtime_login', boundary_sha256 FROM public.mcp_runtime_boundary_receipt;
-- Forward268; the migration runner owns its transaction and ledger stamp.

-- Preserve267 predecessor receipts; no passwords, verifier refresh or old object ownership change.

CREATE ROLE verdify_planner_runtime NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

CREATE ROLE verdify_planner_runtime_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

GRANT verdify_planner_runtime TO verdify_planner_runtime_login WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;

CREATE SCHEMA verdify_planner_runtime AUTHORIZATION verdify;

REVOKE ALL ON SCHEMA verdify_planner_runtime FROM PUBLIC;

GRANT USAGE ON SCHEMA verdify_planner_runtime TO verdify_planner_runtime;

ALTER ROLE verdify_planner_runtime_login SET search_path=verdify_planner_runtime,pg_catalog,public,pg_temp;

CREATE ROLE verdify_setpoint_server_runtime NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

CREATE ROLE verdify_setpoint_server_runtime_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

GRANT verdify_setpoint_server_runtime TO verdify_setpoint_server_runtime_login WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;

CREATE SCHEMA verdify_setpoint_server_runtime AUTHORIZATION verdify;

REVOKE ALL ON SCHEMA verdify_setpoint_server_runtime FROM PUBLIC;

GRANT USAGE ON SCHEMA verdify_setpoint_server_runtime TO verdify_setpoint_server_runtime;

ALTER ROLE verdify_setpoint_server_runtime_login SET search_path=verdify_setpoint_server_runtime,pg_catalog,public,pg_temp;

CREATE ROLE verdify_ha_backfill_runtime NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

CREATE ROLE verdify_ha_backfill_runtime_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

GRANT verdify_ha_backfill_runtime TO verdify_ha_backfill_runtime_login WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;

CREATE SCHEMA verdify_ha_backfill_runtime AUTHORIZATION verdify;

REVOKE ALL ON SCHEMA verdify_ha_backfill_runtime FROM PUBLIC;

GRANT USAGE ON SCHEMA verdify_ha_backfill_runtime TO verdify_ha_backfill_runtime;

ALTER ROLE verdify_ha_backfill_runtime_login SET search_path=verdify_ha_backfill_runtime,pg_catalog,public,pg_temp;

CREATE ROLE verdify_vision_runtime NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

CREATE ROLE verdify_vision_runtime_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

GRANT verdify_vision_runtime TO verdify_vision_runtime_login WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;

CREATE SCHEMA verdify_vision_runtime AUTHORIZATION verdify;

REVOKE ALL ON SCHEMA verdify_vision_runtime FROM PUBLIC;

GRANT USAGE ON SCHEMA verdify_vision_runtime TO verdify_vision_runtime;

ALTER ROLE verdify_vision_runtime_login SET search_path=verdify_vision_runtime,pg_catalog,public,pg_temp;

CREATE ROLE verdify_lab_publisher_runtime NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

CREATE ROLE verdify_lab_publisher_runtime_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

GRANT verdify_lab_publisher_runtime TO verdify_lab_publisher_runtime_login WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;

CREATE SCHEMA verdify_lab_publisher_runtime AUTHORIZATION verdify;

REVOKE ALL ON SCHEMA verdify_lab_publisher_runtime FROM PUBLIC;

GRANT USAGE ON SCHEMA verdify_lab_publisher_runtime TO verdify_lab_publisher_runtime;

ALTER ROLE verdify_lab_publisher_runtime_login SET search_path=verdify_lab_publisher_runtime,pg_catalog,public,pg_temp;

CREATE ROLE verdify_grafana_runtime NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

CREATE ROLE verdify_grafana_runtime_login LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

GRANT verdify_grafana_runtime TO verdify_grafana_runtime_login WITH ADMIN FALSE, INHERIT TRUE, SET TRUE;

CREATE SCHEMA verdify_grafana_runtime AUTHORIZATION verdify;

REVOKE ALL ON SCHEMA verdify_grafana_runtime FROM PUBLIC;

GRANT USAGE ON SCHEMA verdify_grafana_runtime TO verdify_grafana_runtime;

ALTER ROLE verdify_grafana_runtime_login SET search_path=verdify_grafana_runtime,pg_catalog,public,pg_temp;

-- #643 migration-owned planner persistence bootstrap.
-- Existing267 seals must remain unchanged; runtime initialization never performs DDL.
-- Existing rows remain in place; these are the exact current initialize statements.

-- planner_graph/store.py:325
CREATE TABLE IF NOT EXISTS planner_graph_runs (
                        trigger_id UUID PRIMARY KEY,
                        thread_id UUID NOT NULL,
                        status TEXT NOT NULL,
                        run_mode TEXT NOT NULL,
                        current_step TEXT NULL,
                        terminal_status TEXT NULL,
                        execution_owner TEXT NULL,
                        last_error TEXT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        queued BOOLEAN NOT NULL DEFAULT TRUE,
                        submission_count INTEGER NOT NULL DEFAULT 1,
                        state JSONB NOT NULL DEFAULT '{}'::jsonb,
                        lease_owner TEXT NULL,
                        lease_expires_at TIMESTAMPTZ NULL,
                        started_at TIMESTAMPTZ NULL,
                        completed_at TIMESTAMPTZ NULL
                    );

-- planner_graph/store.py:347
CREATE INDEX IF NOT EXISTS planner_graph_runs_status_idx ON planner_graph_runs(status, queued, updated_at);

-- planner_graph/memory.py:237
CREATE TABLE IF NOT EXISTS planner_memory_items (
                        memory_id UUID PRIMARY KEY,
                        greenhouse_id TEXT NOT NULL,
                        memory_type TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        source_id TEXT NULL,
                        trigger_id UUID NULL,
                        event_type TEXT NULL,
                        title TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        body TEXT NOT NULL,
                        tags TEXT[] NOT NULL DEFAULT '{}',
                        importance SMALLINT NOT NULL DEFAULT 3,
                        confidence REAL NULL,
                        trust_level TEXT NOT NULL DEFAULT 'planner_inferred',
                        content_hash TEXT NOT NULL,
                        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        valid_from TIMESTAMPTZ NULL,
                        expires_at TIMESTAMPTZ NULL,
                        last_used_at TIMESTAMPTZ NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        CONSTRAINT planner_memory_items_importance_check
                            CHECK (importance BETWEEN 1 AND 5),
                        CONSTRAINT planner_memory_items_confidence_check
                            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
                        CONSTRAINT planner_memory_items_type_check
                            CHECK (memory_type IN ('lesson', 'support_doc', 'prior_plan', 'observed_outcome', 'planner_summary')),
                        CONSTRAINT planner_memory_items_trust_level_check
                            CHECK (trust_level IN ('observed_outcome', 'verdify_context', 'planner_inferred')),
                        CONSTRAINT planner_memory_items_valid_window_check
                            CHECK (expires_at IS NULL OR valid_from IS NULL OR expires_at > valid_from)
                    );

-- planner_graph/memory.py:275
DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint
                             WHERE conrelid = 'planner_memory_items'::regclass
                               AND conname = 'planner_memory_items_importance_check'
                        ) THEN
                            ALTER TABLE planner_memory_items
                            ADD CONSTRAINT planner_memory_items_importance_check
                            CHECK (importance BETWEEN 1 AND 5);
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint
                             WHERE conrelid = 'planner_memory_items'::regclass
                               AND conname = 'planner_memory_items_confidence_check'
                        ) THEN
                            ALTER TABLE planner_memory_items
                            ADD CONSTRAINT planner_memory_items_confidence_check
                            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));
                        END IF;

                        ALTER TABLE planner_memory_items
                        DROP CONSTRAINT IF EXISTS planner_memory_items_type_check;
                        ALTER TABLE planner_memory_items
                        ADD CONSTRAINT planner_memory_items_type_check
                        CHECK (memory_type IN ('lesson', 'support_doc', 'prior_plan', 'observed_outcome', 'planner_summary'));

                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint
                             WHERE conrelid = 'planner_memory_items'::regclass
                               AND conname = 'planner_memory_items_trust_level_check'
                        ) THEN
                            ALTER TABLE planner_memory_items
                            ADD CONSTRAINT planner_memory_items_trust_level_check
                            CHECK (trust_level IN ('observed_outcome', 'verdify_context', 'planner_inferred'));
                        END IF;

                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint
                             WHERE conrelid = 'planner_memory_items'::regclass
                               AND conname = 'planner_memory_items_valid_window_check'
                        ) THEN
                            ALTER TABLE planner_memory_items
                            ADD CONSTRAINT planner_memory_items_valid_window_check
                            CHECK (expires_at IS NULL OR valid_from IS NULL OR expires_at > valid_from);
                        END IF;
                    END $$;

-- planner_graph/memory.py:327
CREATE UNIQUE INDEX IF NOT EXISTS planner_memory_items_unique_content_idx
                    ON planner_memory_items(greenhouse_id, memory_type, content_hash);

-- planner_graph/memory.py:333
CREATE UNIQUE INDEX IF NOT EXISTS planner_memory_items_unique_source_idx
                    ON planner_memory_items(greenhouse_id, source_type, source_id)
                    WHERE source_id IS NOT NULL;

-- planner_graph/memory.py:340
CREATE INDEX IF NOT EXISTS planner_memory_items_lookup_idx
                    ON planner_memory_items(greenhouse_id, memory_type, event_type, created_at DESC);

-- planner_graph/memory.py:346
CREATE INDEX IF NOT EXISTS planner_memory_items_search_idx
                    ON planner_memory_items
                    USING GIN (
                        to_tsvector(
                            'english',
                            coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(body, '')
                        )
                    );

-- planner_graph/memory.py:358
CREATE TABLE IF NOT EXISTS planner_memory_retrievals (
                        retrieval_id UUID PRIMARY KEY,
                        trigger_id UUID NULL,
                        greenhouse_id TEXT NOT NULL,
                        strategy TEXT NOT NULL,
                        query_text TEXT NOT NULL,
                        filters JSONB NOT NULL DEFAULT '{}'::jsonb,
                        result_ids UUID[] NOT NULL DEFAULT '{}',
                        scores JSONB NOT NULL DEFAULT '{}'::jsonb,
                        latency_ms INT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );

-- planner_graph/memory.py:374
CREATE INDEX IF NOT EXISTS planner_memory_retrievals_trigger_idx
                    ON planner_memory_retrievals(trigger_id, created_at DESC);

-- planner_graph/memory.py:380
CREATE INDEX IF NOT EXISTS planner_memory_retrievals_greenhouse_idx
                    ON planner_memory_retrievals(greenhouse_id, created_at DESC);


GRANT SELECT,INSERT,UPDATE ON public.planner_graph_runs, public.planner_memory_items TO verdify_planner_runtime;

GRANT SELECT,INSERT ON public.planner_memory_retrievals TO verdify_planner_runtime;

GRANT SELECT,INSERT,UPDATE ON public.daily_plan_archive_audit TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_planner_runtime.alert_log WITH(security_barrier=true) AS SELECT "id", "ts", "alert_type", "severity", "sensor_id", "zone", "message", "details", "source", "disposition", "acknowledged_at", "acknowledged_by", "resolved_at", "resolved_by", "resolution", "slack_ts", "created_at", "updated_at", "category", "metric_value", "threshold_value", "notes", "greenhouse_id", "zone_id", "slack_channel_id", "slack_message_ts", "slack_thread_ts", "slack_last_posted_at", "slack_snoozed_until", "slack_snoozed_by", "slack_assigned_to" FROM public.alert_log;

REVOKE ALL ON verdify_planner_runtime.alert_log FROM PUBLIC;

GRANT SELECT ON verdify_planner_runtime.alert_log TO verdify_planner_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_planner_runtime.climate WITH(security_barrier=true) AS SELECT "ts", "temp_avg", "temp_north", "temp_south", "temp_east", "temp_west", "temp_case", "temp_control", "temp_intake", "rh_avg", "rh_north", "rh_south", "rh_east", "rh_west", "rh_case", "vpd_avg", "vpd_north", "vpd_south", "vpd_east", "vpd_west", "vpd_control", "dew_point", "abs_humidity", "enthalpy_delta", "co2_ppm", "lux", "dli_today", "flow_gpm", "water_total_gal", "mister_water_today", "outdoor_temp_f", "outdoor_rh_pct", "ph_input", "ec_input", "ph_runoff_wall", "ec_runoff_wall", "ph_runoff_center", "ec_runoff_center", "moisture_north", "moisture_south", "moisture_center", "ppfd", "dli_par_today", "pressure_hpa", "leaf_temp_north", "leaf_temp_south", "leaf_wetness_north", "leaf_wetness_south", "wind_speed_mph", "wind_direction_deg", "outdoor_lux", "solar_irradiance_w_m2", "precip_in", "uv_index", "hydro_tds_ppm", "hydro_water_temp_f", "wind_gust_mph", "wind_lull_mph", "wind_speed_avg_mph", "wind_direction_avg_deg", "feels_like_f", "wet_bulb_temp_f", "vapor_pressure_inhg", "air_density_kg_m3", "precip_intensity_in_h", "lightning_count", "lightning_avg_dist_mi", "solar_altitude_deg", "solar_azimuth_deg", "hydro_ec_us_cm", "hydro_orp_mv", "hydro_ph", "hydro_battery_pct", "soil_moisture_south_1", "soil_temp_south_1", "soil_ec_south_1", "soil_moisture_south_2", "soil_temp_south_2", "soil_moisture_west", "soil_temp_west", "intake_rh", "intake_vpd", "outdoor_illuminance", "greenhouse_id", "solar_phase", "solar_sunrise_min", "solar_noon_min", "solar_sunset_min", "house_temp_target_f", "house_temp_delta_f", "house_vpd_target", "house_vpd_delta", "vpd_target_center", "vpd_target_south", "vpd_target_west", "vpd_target_east", "vpd_delta_center", "vpd_delta_south", "vpd_delta_west", "vpd_delta_east" FROM public.climate;

REVOKE ALL ON verdify_planner_runtime.climate FROM PUBLIC;

GRANT SELECT ON verdify_planner_runtime.climate TO verdify_planner_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_planner_runtime.plan_delivery_log WITH(security_barrier=true) AS SELECT "id", "delivered_at", "event_type", "event_label", "session_key", "wake_mode", "gateway_status", "gateway_body", "resulting_plan_id", "plan_written_at", "greenhouse_id", "trigger_id", "instance", "acked_at", "status", "hermes_run_id", "terminal_action", "terminal_at", "failure_class", "result_payload" FROM public.plan_delivery_log;

REVOKE ALL ON verdify_planner_runtime.plan_delivery_log FROM PUBLIC;

GRANT SELECT ON verdify_planner_runtime.plan_delivery_log TO verdify_planner_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_planner_runtime.plan_journal WITH(security_barrier=true) AS SELECT "plan_id", "created_at", "conditions_summary", "hypothesis", "experiment", "expected_outcome", "params_changed", "actual_outcome", "outcome_score", "lesson_extracted", "validated_at", "greenhouse_id", "hypothesis_structured", "planner_instance", "trigger_id", "anchor_score", "climate_intents", "climate_intent_version", "guardrail_penalty", "valid_from", "expires_at", "lifecycle_status" FROM public.plan_journal;

REVOKE ALL ON verdify_planner_runtime.plan_journal FROM PUBLIC;

GRANT SELECT ON verdify_planner_runtime.plan_journal TO verdify_planner_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_planner_runtime.setpoint_clamps WITH(security_barrier=true) AS SELECT "ts", "parameter", "requested", "applied", "band_lo", "band_hi", "reason", "greenhouse_id", "status", "plan_id", "plan_ts", "trigger_id", "planner_instance" FROM public.setpoint_clamps;

REVOKE ALL ON verdify_planner_runtime.setpoint_clamps FROM PUBLIC;

GRANT SELECT ON verdify_planner_runtime.setpoint_clamps TO verdify_planner_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_planner_runtime.setpoint_plan WITH(security_barrier=true) AS SELECT "ts", "parameter", "value", "plan_id", "source", "reason", "created_at", "is_active", "greenhouse_id", "trigger_id", "planner_instance", "expires_at" FROM public.setpoint_plan;

REVOKE ALL ON verdify_planner_runtime.setpoint_plan FROM PUBLIC;

GRANT SELECT ON verdify_planner_runtime.setpoint_plan TO verdify_planner_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_planner_runtime.setpoint_snapshot WITH(security_barrier=true) AS SELECT "ts", "parameter", "value", "greenhouse_id", "zone", "band_role", "target_value" FROM public.setpoint_snapshot;

REVOKE ALL ON verdify_planner_runtime.setpoint_snapshot FROM PUBLIC;

GRANT SELECT ON verdify_planner_runtime.setpoint_snapshot TO verdify_planner_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_planner_runtime.weather_forecast WITH(security_barrier=true) AS SELECT "ts", "fetched_at", "temp_f", "rh_pct", "wind_speed_mph", "wind_dir_deg", "cloud_cover_pct", "precip_prob_pct", "solar_w_m2", "dew_point_f", "feels_like_f", "vpd_kpa", "precip_in", "rain_in", "snow_in", "wind_gust_mph", "uv_index", "et0_mm", "direct_radiation_w_m2", "diffuse_radiation_w_m2", "sunshine_duration_s", "weather_code", "cloud_cover_low_pct", "cloud_cover_high_pct", "surface_pressure_hpa", "soil_temp_f", "visibility_m", "greenhouse_id" FROM public.weather_forecast;

REVOKE ALL ON verdify_planner_runtime.weather_forecast FROM PUBLIC;

GRANT SELECT ON verdify_planner_runtime.weather_forecast TO verdify_planner_runtime;

CREATE FUNCTION verdify_planner_runtime.fn_planner_scorecard(p_date date DEFAULT CURRENT_DATE) RETURNS TABLE(metric text, value numeric) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT * FROM public.fn_planner_scorecard($1) $call$;

REVOKE ALL ON FUNCTION verdify_planner_runtime.fn_planner_scorecard(p_date date) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_planner_runtime.fn_planner_scorecard(p_date date) TO verdify_planner_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_setpoint_server_runtime.climate WITH(security_barrier=true) AS SELECT "ts", "temp_avg", "temp_north", "temp_south", "temp_east", "temp_west", "temp_case", "temp_control", "temp_intake", "rh_avg", "rh_north", "rh_south", "rh_east", "rh_west", "rh_case", "vpd_avg", "vpd_north", "vpd_south", "vpd_east", "vpd_west", "vpd_control", "dew_point", "abs_humidity", "enthalpy_delta", "co2_ppm", "lux", "dli_today", "flow_gpm", "water_total_gal", "mister_water_today", "outdoor_temp_f", "outdoor_rh_pct", "ph_input", "ec_input", "ph_runoff_wall", "ec_runoff_wall", "ph_runoff_center", "ec_runoff_center", "moisture_north", "moisture_south", "moisture_center", "ppfd", "dli_par_today", "pressure_hpa", "leaf_temp_north", "leaf_temp_south", "leaf_wetness_north", "leaf_wetness_south", "wind_speed_mph", "wind_direction_deg", "outdoor_lux", "solar_irradiance_w_m2", "precip_in", "uv_index", "hydro_tds_ppm", "hydro_water_temp_f", "wind_gust_mph", "wind_lull_mph", "wind_speed_avg_mph", "wind_direction_avg_deg", "feels_like_f", "wet_bulb_temp_f", "vapor_pressure_inhg", "air_density_kg_m3", "precip_intensity_in_h", "lightning_count", "lightning_avg_dist_mi", "solar_altitude_deg", "solar_azimuth_deg", "hydro_ec_us_cm", "hydro_orp_mv", "hydro_ph", "hydro_battery_pct", "soil_moisture_south_1", "soil_temp_south_1", "soil_ec_south_1", "soil_moisture_south_2", "soil_temp_south_2", "soil_moisture_west", "soil_temp_west", "intake_rh", "intake_vpd", "outdoor_illuminance", "greenhouse_id", "solar_phase", "solar_sunrise_min", "solar_noon_min", "solar_sunset_min", "house_temp_target_f", "house_temp_delta_f", "house_vpd_target", "house_vpd_delta", "vpd_target_center", "vpd_target_south", "vpd_target_west", "vpd_target_east", "vpd_delta_center", "vpd_delta_south", "vpd_delta_west", "vpd_delta_east" FROM public.climate;

REVOKE ALL ON verdify_setpoint_server_runtime.climate FROM PUBLIC;

GRANT SELECT ON verdify_setpoint_server_runtime.climate TO verdify_setpoint_server_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_setpoint_server_runtime.equipment_state WITH(security_barrier=true) AS SELECT "ts", "equipment", "state", "greenhouse_id" FROM public.equipment_state;

REVOKE ALL ON verdify_setpoint_server_runtime.equipment_state FROM PUBLIC;

GRANT SELECT ON verdify_setpoint_server_runtime.equipment_state TO verdify_setpoint_server_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_setpoint_server_runtime.setpoint_changes WITH(security_barrier=true) AS SELECT "ts", "parameter", "value", "source", "greenhouse_id", "confirmed_at", "planner_instance", "trigger_id", "delivery_status", "expired_at", "superseded_by_ts", "zone" FROM public.setpoint_changes;

REVOKE ALL ON verdify_setpoint_server_runtime.setpoint_changes FROM PUBLIC;

GRANT SELECT ON verdify_setpoint_server_runtime.setpoint_changes TO verdify_setpoint_server_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_setpoint_server_runtime.system_state WITH(security_barrier=true) AS SELECT "ts", "entity", "value", "greenhouse_id" FROM public.system_state;

REVOKE ALL ON verdify_setpoint_server_runtime.system_state FROM PUBLIC;

GRANT SELECT ON verdify_setpoint_server_runtime.system_state TO verdify_setpoint_server_runtime;

CREATE FUNCTION verdify_setpoint_server_runtime.fn_read_v_active_plan() RETURNS TABLE("parameter" text, "value" double precision, "ts" timestamp with time zone, "plan_id" text, "reason" text, "created_at" timestamp with time zone, "trigger_id" uuid, "planner_instance" text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "parameter", "value", "ts", "plan_id", "reason", "created_at", "trigger_id", "planner_instance" FROM public.v_active_plan $read$;

REVOKE ALL ON FUNCTION verdify_setpoint_server_runtime.fn_read_v_active_plan() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_setpoint_server_runtime.fn_read_v_active_plan() TO verdify_setpoint_server_runtime;

CREATE VIEW verdify_setpoint_server_runtime.v_active_plan WITH(security_barrier=true) AS SELECT * FROM verdify_setpoint_server_runtime.fn_read_v_active_plan();

REVOKE ALL ON verdify_setpoint_server_runtime.v_active_plan FROM PUBLIC;

GRANT SELECT ON verdify_setpoint_server_runtime.v_active_plan TO verdify_setpoint_server_runtime;

CREATE FUNCTION verdify_setpoint_server_runtime.fn_lighting_minutes_policy(p_ts timestamp with time zone DEFAULT now(), p_greenhouse_id text DEFAULT 'vallery'::text) RETURNS TABLE(greenhouse_id text, ts timestamp with time zone, light_key text, equipment text, target_light_minutes integer, start_hour integer, cutoff_hour integer, lux_on_threshold double precision, lux_hysteresis double precision, lux_off_threshold double precision, min_on_s integer, min_off_s integer, auto_enabled boolean, legacy_dli_target double precision, source_chain text, controller_contract text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT * FROM public.fn_lighting_minutes_policy($1,$2) $call$;

REVOKE ALL ON FUNCTION verdify_setpoint_server_runtime.fn_lighting_minutes_policy(p_ts timestamp with time zone, p_greenhouse_id text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_setpoint_server_runtime.fn_lighting_minutes_policy(p_ts timestamp with time zone, p_greenhouse_id text) TO verdify_setpoint_server_runtime;

CREATE FUNCTION verdify_setpoint_server_runtime.fn_house_vpd_control_band(target_ts timestamp with time zone) RETURNS TABLE(crop_vpd_low double precision, crop_vpd_high double precision, vpd_target_south double precision, vpd_target_west double precision, vpd_target_east double precision, vpd_target_center double precision, zone_vpd_min double precision, zone_vpd_median double precision, zone_vpd_max double precision, house_vpd_low double precision, house_vpd_high double precision, house_vpd_min_width_kpa double precision, house_vpd_low_margin_kpa double precision) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT * FROM public.fn_house_vpd_control_band($1) $call$;

REVOKE ALL ON FUNCTION verdify_setpoint_server_runtime.fn_house_vpd_control_band(target_ts timestamp with time zone) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_setpoint_server_runtime.fn_house_vpd_control_band(target_ts timestamp with time zone) TO verdify_setpoint_server_runtime;

CREATE FUNCTION verdify_setpoint_server_runtime.fn_band_setpoints(target_ts timestamp with time zone) RETURNS TABLE(temp_low double precision, temp_high double precision, vpd_low double precision, vpd_high double precision, temp_target double precision, vpd_target double precision) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT * FROM public.fn_band_setpoints($1) $call$;

REVOKE ALL ON FUNCTION verdify_setpoint_server_runtime.fn_band_setpoints(target_ts timestamp with time zone) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_setpoint_server_runtime.fn_band_setpoints(target_ts timestamp with time zone) TO verdify_setpoint_server_runtime;

CREATE FUNCTION verdify_setpoint_server_runtime.fn_zone_vpd_targets(target_ts timestamp with time zone) RETURNS TABLE(vpd_target_south double precision, vpd_target_west double precision, vpd_target_east double precision, vpd_target_center double precision) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT * FROM public.fn_zone_vpd_targets($1) $call$;

REVOKE ALL ON FUNCTION verdify_setpoint_server_runtime.fn_zone_vpd_targets(target_ts timestamp with time zone) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_setpoint_server_runtime.fn_zone_vpd_targets(target_ts timestamp with time zone) TO verdify_setpoint_server_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_vision_runtime.camera_zone_map WITH(security_barrier=true) AS SELECT "camera", "zone", "coverage_pct" FROM public.camera_zone_map;

REVOKE ALL ON verdify_vision_runtime.camera_zone_map FROM PUBLIC;

GRANT SELECT ON verdify_vision_runtime.camera_zone_map TO verdify_vision_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_vision_runtime.climate WITH(security_barrier=true) AS SELECT "ts", "temp_avg", "rh_avg", "vpd_avg", "lux", "soil_moisture_south_1", "soil_moisture_west" FROM public.climate WHERE temp_avg IS NOT NULL ORDER BY ts DESC LIMIT 1;

REVOKE ALL ON verdify_vision_runtime.climate FROM PUBLIC;

GRANT SELECT ON verdify_vision_runtime.climate TO verdify_vision_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_vision_runtime.crops WITH(security_barrier=true) AS SELECT "id", "name", "position", "zone", "stage", "notes", "is_active", "greenhouse_id", "position_id", "zone_id" FROM public.crops WHERE is_active;

REVOKE ALL ON verdify_vision_runtime.crops FROM PUBLIC;

GRANT SELECT ON verdify_vision_runtime.crops TO verdify_vision_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.alert_log WITH(security_barrier=true) AS SELECT "id", "ts", "alert_type", "severity", "sensor_id", "zone", "message", "details", "source", "disposition", "acknowledged_at", "acknowledged_by", "resolved_at", "resolved_by", "resolution", "slack_ts", "created_at", "updated_at", "category", "metric_value", "threshold_value", "notes", "greenhouse_id", "zone_id", "slack_channel_id", "slack_message_ts", "slack_thread_ts", "slack_last_posted_at", "slack_snoozed_until", "slack_snoozed_by", "slack_assigned_to" FROM public.alert_log;

REVOKE ALL ON verdify_lab_publisher_runtime.alert_log FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.alert_log TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.climate WITH(security_barrier=true) AS SELECT "ts", "temp_avg", "temp_north", "temp_south", "temp_east", "temp_west", "temp_case", "temp_control", "temp_intake", "rh_avg", "rh_north", "rh_south", "rh_east", "rh_west", "rh_case", "vpd_avg", "vpd_north", "vpd_south", "vpd_east", "vpd_west", "vpd_control", "dew_point", "abs_humidity", "enthalpy_delta", "co2_ppm", "lux", "dli_today", "flow_gpm", "water_total_gal", "mister_water_today", "outdoor_temp_f", "outdoor_rh_pct", "ph_input", "ec_input", "ph_runoff_wall", "ec_runoff_wall", "ph_runoff_center", "ec_runoff_center", "moisture_north", "moisture_south", "moisture_center", "ppfd", "dli_par_today", "pressure_hpa", "leaf_temp_north", "leaf_temp_south", "leaf_wetness_north", "leaf_wetness_south", "wind_speed_mph", "wind_direction_deg", "outdoor_lux", "solar_irradiance_w_m2", "precip_in", "uv_index", "hydro_tds_ppm", "hydro_water_temp_f", "wind_gust_mph", "wind_lull_mph", "wind_speed_avg_mph", "wind_direction_avg_deg", "feels_like_f", "wet_bulb_temp_f", "vapor_pressure_inhg", "air_density_kg_m3", "precip_intensity_in_h", "lightning_count", "lightning_avg_dist_mi", "solar_altitude_deg", "solar_azimuth_deg", "hydro_ec_us_cm", "hydro_orp_mv", "hydro_ph", "hydro_battery_pct", "soil_moisture_south_1", "soil_temp_south_1", "soil_ec_south_1", "soil_moisture_south_2", "soil_temp_south_2", "soil_moisture_west", "soil_temp_west", "intake_rh", "intake_vpd", "outdoor_illuminance", "greenhouse_id", "solar_phase", "solar_sunrise_min", "solar_noon_min", "solar_sunset_min", "house_temp_target_f", "house_temp_delta_f", "house_vpd_target", "house_vpd_delta", "vpd_target_center", "vpd_target_south", "vpd_target_west", "vpd_target_east", "vpd_delta_center", "vpd_delta_south", "vpd_delta_west", "vpd_delta_east" FROM public.climate;

REVOKE ALL ON verdify_lab_publisher_runtime.climate FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.climate TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.crop_catalog WITH(security_barrier=true) AS SELECT "id", "slug", "common_name", "scientific_name", "category", "season", "cycle_days_min", "cycle_days_max", "base_temp_f", "default_target_dli", "default_target_vpd_low", "default_target_vpd_high", "default_ph_low", "default_ph_high", "default_ec_low", "default_ec_high", "notes", "created_at" FROM public.crop_catalog;

REVOKE ALL ON verdify_lab_publisher_runtime.crop_catalog FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.crop_catalog TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.crop_events WITH(security_barrier=true) AS SELECT "id", "ts", "crop_id", "event_type", "old_stage", "new_stage", "count", "operator", "source", "notes", "greenhouse_id", "position_id", "slack_channel_id", "slack_message_ts", "slack_thread_ts", "slack_user_id" FROM public.crop_events;

REVOKE ALL ON verdify_lab_publisher_runtime.crop_events FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.crop_events TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.crops WITH(security_barrier=true) AS SELECT "id", "name", "variety", "position", "zone", "planted_date", "expected_harvest", "stage", "count", "seed_lot_id", "supplier", "base_temp_f", "target_dli", "target_vpd_low", "target_vpd_high", "notes", "is_active", "created_at", "updated_at", "greenhouse_id", "position_id", "zone_id", "crop_catalog_id", "cleared_at" FROM public.crops;

REVOKE ALL ON verdify_lab_publisher_runtime.crops FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.crops TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.daily_summary WITH(security_barrier=true) AS SELECT "date", "cycles_fan1", "cycles_fan2", "cycles_heat1", "cycles_heat2", "cycles_fog", "cycles_vent", "cycles_dehum", "cycles_safety_dehum", "runtime_fan1_min", "runtime_fan2_min", "runtime_heat1_min", "runtime_heat2_min", "runtime_fog_min", "runtime_vent_min", "runtime_mister_south_h", "runtime_mister_west_h", "runtime_mister_center_h", "water_used_gal", "mister_water_gal", "dli_final", "captured_at", "kwh_total", "kwh_heat", "kwh_fans", "kwh_other", "peak_kw", "gas_used_therms", "runtime_grow_light_min", "cycles_grow_light", "runtime_drip_wall_h", "runtime_drip_center_h", "kwh_estimated", "therms_estimated", "cost_electric", "cost_gas", "cost_water", "cost_total", "temp_min", "temp_max", "temp_avg", "rh_min", "rh_max", "rh_avg", "vpd_min", "vpd_max", "vpd_avg", "co2_avg", "outdoor_temp_min", "outdoor_temp_max", "stress_hours_heat", "stress_hours_cold", "stress_hours_vpd_high", "stress_hours_vpd_low", "notes", "greenhouse_id", "min_dp_margin_f", "dp_risk_hours", "compliance_pct", "temp_compliance_pct", "vpd_compliance_pct", "mister_fairness_overrides_today", "cycles_mister_south", "cycles_mister_west", "cycles_mister_center", "cycles_drip_wall", "cycles_drip_center", "runtime_drip_wall_fert_h", "runtime_drip_center_fert_h", "runtime_mister_south_fert_h", "runtime_mister_west_fert_h", "runtime_fert_master_h", "runtime_irrigation_clean_h", "runtime_irrigation_fert_h", "runtime_irrigation_total_h", "cycles_drip_wall_fert", "cycles_drip_center_fert", "cycles_mister_south_fert", "cycles_mister_west_fert", "cycles_fert_master", "irrigation_water_gal", "fertigation_water_gal", "compliance_v2_raw_pct", "compliance_v2_attributable_pct", "compliance_v2_unachievable_frac", "graded_temp_compliance_pct", "graded_vpd_compliance_pct", "graded_stress_hours_heat", "graded_stress_hours_cold", "graded_stress_hours_vpd_high", "graded_stress_hours_vpd_low", "feasibility_unknown_min", "dev_temp_norm_median_day", "dev_temp_norm_median_night", "dev_temp_norm_p95", "dev_vpd_norm_median_day", "dev_vpd_norm_median_night", "dev_vpd_norm_p95", "runtime_grow_light_main_min", "runtime_grow_light_grow_min", "climate_observed_minute_metrics" FROM public.daily_summary;

REVOKE ALL ON verdify_lab_publisher_runtime.daily_summary FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.daily_summary TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.equipment WITH(security_barrier=true) AS SELECT "id", "greenhouse_id", "slug", "zone_id", "kind", "name", "model", "manufacturer", "watts", "cost_per_hour_usd", "specs", "install_date", "is_active", "notes", "created_at" FROM public.equipment;

REVOKE ALL ON verdify_lab_publisher_runtime.equipment FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.equipment TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.equipment_state WITH(security_barrier=true) AS SELECT "ts", "equipment", "state", "greenhouse_id" FROM public.equipment_state;

REVOKE ALL ON verdify_lab_publisher_runtime.equipment_state FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.equipment_state TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.forecast_deviation_log WITH(security_barrier=true) AS SELECT "ts", "parameter", "observed", "forecasted", "delta", "threshold", "triggered", "greenhouse_id" FROM public.forecast_deviation_log;

REVOKE ALL ON verdify_lab_publisher_runtime.forecast_deviation_log FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.forecast_deviation_log TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.image_observations WITH(security_barrier=true) AS SELECT "id", "ts", "camera", "zone", "image_path", "model", "raw_response", "crops_observed", "environment_notes", "recommended_actions", "processing_ms", "tokens_used", "confidence", "embedding", "greenhouse_id", "zone_id" FROM public.image_observations;

REVOKE ALL ON verdify_lab_publisher_runtime.image_observations FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.image_observations TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.observations WITH(security_barrier=true) AS SELECT "id", "ts", "obs_type", "zone", "position", "severity", "species", "count", "affected_pct", "crop_id", "photo_path", "observer", "source", "notes", "image_observation_id", "health_score", "greenhouse_id", "position_id", "zone_id", "plant_height_cm", "leaf_count", "canopy_cover_pct", "flowering_count", "fruit_count", "root_condition", "mortality_count", "stress_tags", "slack_channel_id", "slack_message_ts", "slack_thread_ts", "slack_user_id", "slack_file_ids", "slack_file_refs" FROM public.observations;

REVOKE ALL ON verdify_lab_publisher_runtime.observations FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.observations TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.override_events WITH(security_barrier=true) AS SELECT "ts", "override_type", "mode", "details", "greenhouse_id" FROM public.override_events;

REVOKE ALL ON verdify_lab_publisher_runtime.override_events FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.override_events TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.plan_delivery_log WITH(security_barrier=true) AS SELECT "id", "delivered_at", "event_type", "event_label", "session_key", "wake_mode", "gateway_status", "gateway_body", "resulting_plan_id", "plan_written_at", "greenhouse_id", "trigger_id", "instance", "acked_at", "status", "hermes_run_id", "terminal_action", "terminal_at", "failure_class", "result_payload" FROM public.plan_delivery_log;

REVOKE ALL ON verdify_lab_publisher_runtime.plan_delivery_log FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.plan_delivery_log TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.plan_journal WITH(security_barrier=true) AS SELECT "plan_id", "created_at", "conditions_summary", "hypothesis", "experiment", "expected_outcome", "params_changed", "actual_outcome", "outcome_score", "lesson_extracted", "validated_at", "greenhouse_id", "hypothesis_structured", "planner_instance", "trigger_id", "anchor_score", "climate_intents", "climate_intent_version", "guardrail_penalty", "valid_from", "expires_at", "lifecycle_status" FROM public.plan_journal;

REVOKE ALL ON verdify_lab_publisher_runtime.plan_journal FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.plan_journal TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.planner_lessons WITH(security_barrier=true) AS SELECT "id", "created_at", "category", "condition", "lesson", "confidence", "times_validated", "last_validated", "source_plan_ids", "superseded_by", "is_active", "greenhouse_id" FROM public.planner_lessons;

REVOKE ALL ON verdify_lab_publisher_runtime.planner_lessons FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.planner_lessons TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.planner_trigger_ledger WITH(security_barrier=true) AS SELECT "id", "greenhouse_id", "event_type", "event_label", "instance", "expected_at", "due_at", "delivered_at", "resolved_at", "status", "expected_action", "sla_seconds", "catchup", "plan_delivery_log_id", "trigger_id", "resulting_plan_id", "notes", "created_at", "updated_at", "terminal_action", "terminal_at", "failure_class", "had_required_failure" FROM public.planner_trigger_ledger;

REVOKE ALL ON verdify_lab_publisher_runtime.planner_trigger_ledger FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.planner_trigger_ledger TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.setpoint_changes WITH(security_barrier=true) AS SELECT "ts", "parameter", "value", "source", "greenhouse_id", "confirmed_at", "planner_instance", "trigger_id", "delivery_status", "expired_at", "superseded_by_ts", "zone" FROM public.setpoint_changes;

REVOKE ALL ON verdify_lab_publisher_runtime.setpoint_changes FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.setpoint_changes TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.setpoint_clamps WITH(security_barrier=true) AS SELECT "ts", "parameter", "requested", "applied", "band_lo", "band_hi", "reason", "greenhouse_id", "status", "plan_id", "plan_ts", "trigger_id", "planner_instance" FROM public.setpoint_clamps;

REVOKE ALL ON verdify_lab_publisher_runtime.setpoint_clamps FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.setpoint_clamps TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.setpoint_plan WITH(security_barrier=true) AS SELECT "ts", "parameter", "value", "plan_id", "source", "reason", "created_at", "is_active", "greenhouse_id", "trigger_id", "planner_instance", "expires_at" FROM public.setpoint_plan;

REVOKE ALL ON verdify_lab_publisher_runtime.setpoint_plan FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.setpoint_plan TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.setpoint_snapshot WITH(security_barrier=true) AS SELECT "ts", "parameter", "value", "greenhouse_id", "zone", "band_role", "target_value" FROM public.setpoint_snapshot;

REVOKE ALL ON verdify_lab_publisher_runtime.setpoint_snapshot FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.setpoint_snapshot TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.system_state WITH(security_barrier=true) AS SELECT "ts", "entity", "value", "greenhouse_id" FROM public.system_state;

REVOKE ALL ON verdify_lab_publisher_runtime.system_state FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.system_state TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_read_v_active_plan() RETURNS TABLE("parameter" text, "value" double precision, "ts" timestamp with time zone, "plan_id" text, "reason" text, "created_at" timestamp with time zone, "trigger_id" uuid, "planner_instance" text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "parameter", "value", "ts", "plan_id", "reason", "created_at", "trigger_id", "planner_instance" FROM public.v_active_plan $read$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_active_plan() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_active_plan() TO verdify_lab_publisher_runtime;

CREATE VIEW verdify_lab_publisher_runtime.v_active_plan WITH(security_barrier=true) AS SELECT * FROM verdify_lab_publisher_runtime.fn_read_v_active_plan();

REVOKE ALL ON verdify_lab_publisher_runtime.v_active_plan FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.v_active_plan TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_read_v_crop_catalog_with_profiles() RETURNS TABLE("crop_catalog_id" integer, "slug" text, "common_name" text, "scientific_name" text, "category" text, "season" text, "cycle_days_min" integer, "cycle_days_max" integer, "base_temp_f" double precision, "default_target_dli" double precision, "default_target_vpd_low" double precision, "default_target_vpd_high" double precision, "default_ph_low" double precision, "default_ph_high" double precision, "default_ec_low" double precision, "default_ec_high" double precision, "stage_season_profiles" jsonb) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "crop_catalog_id", "slug", "common_name", "scientific_name", "category", "season", "cycle_days_min", "cycle_days_max", "base_temp_f", "default_target_dli", "default_target_vpd_low", "default_target_vpd_high", "default_ph_low", "default_ph_high", "default_ec_low", "default_ec_high", "stage_season_profiles" FROM public.v_crop_catalog_with_profiles $read$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_crop_catalog_with_profiles() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_crop_catalog_with_profiles() TO verdify_lab_publisher_runtime;

CREATE VIEW verdify_lab_publisher_runtime.v_crop_catalog_with_profiles WITH(security_barrier=true) AS SELECT * FROM verdify_lab_publisher_runtime.fn_read_v_crop_catalog_with_profiles();

REVOKE ALL ON verdify_lab_publisher_runtime.v_crop_catalog_with_profiles FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.v_crop_catalog_with_profiles TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_read_v_crop_history() RETURNS TABLE("position_id" integer, "greenhouse_id" text, "position_label" text, "zone_slug" text, "crop_id" integer, "crop_name" text, "crop_variety" text, "final_stage" text, "planted_date" date, "cleared_at" timestamp with time zone, "is_active" boolean, "days_in_place" integer, "crop_catalog_slug" text, "crop_common_name" text, "event_count" bigint, "observation_count" bigint, "harvest_count" bigint) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "position_id", "greenhouse_id", "position_label", "zone_slug", "crop_id", "crop_name", "crop_variety", "final_stage", "planted_date", "cleared_at", "is_active", "days_in_place", "crop_catalog_slug", "crop_common_name", "event_count", "observation_count", "harvest_count" FROM public.v_crop_history $read$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_crop_history() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_crop_history() TO verdify_lab_publisher_runtime;

CREATE VIEW verdify_lab_publisher_runtime.v_crop_history WITH(security_barrier=true) AS SELECT * FROM verdify_lab_publisher_runtime.fn_read_v_crop_history();

REVOKE ALL ON verdify_lab_publisher_runtime.v_crop_history FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.v_crop_history TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_read_v_dli_daily() RETURNS TABLE("date" date, "greenhouse_id" text, "crop_dli_mol_m2_day" double precision, "availability" text, "unavailable_reason" text, "provenance" text, "validity_revision" text, "valid_from" timestamp with time zone, "valid_to" timestamp with time zone, "forensic_proxy_present" boolean) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "date", "greenhouse_id", "crop_dli_mol_m2_day", "availability", "unavailable_reason", "provenance", "validity_revision", "valid_from", "valid_to", "forensic_proxy_present" FROM public.v_dli_daily $read$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_dli_daily() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_dli_daily() TO verdify_lab_publisher_runtime;

CREATE VIEW verdify_lab_publisher_runtime.v_dli_daily WITH(security_barrier=true) AS SELECT * FROM verdify_lab_publisher_runtime.fn_read_v_dli_daily();

REVOKE ALL ON verdify_lab_publisher_runtime.v_dli_daily FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.v_dli_daily TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_read_v_equipment_relay_map() RETURNS TABLE("greenhouse_id" text, "board" text, "pin" integer, "switch_slug" text, "equipment_slug" text, "equipment_name" text, "equipment_kind" text, "model" text, "zone_slug" text, "zone_name" text, "purpose" text, "state_source_column" text, "is_active" boolean) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "greenhouse_id", "board", "pin", "switch_slug", "equipment_slug", "equipment_name", "equipment_kind", "model", "zone_slug", "zone_name", "purpose", "state_source_column", "is_active" FROM public.v_equipment_relay_map $read$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_equipment_relay_map() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_equipment_relay_map() TO verdify_lab_publisher_runtime;

CREATE VIEW verdify_lab_publisher_runtime.v_equipment_relay_map WITH(security_barrier=true) AS SELECT * FROM verdify_lab_publisher_runtime.fn_read_v_equipment_relay_map();

REVOKE ALL ON verdify_lab_publisher_runtime.v_equipment_relay_map FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.v_equipment_relay_map TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_read_v_equipment_resource_catalog() RETURNS TABLE("greenhouse_id" text, "equipment_id" integer, "equipment_slug" text, "equipment_kind" text, "equipment_name" text, "resource_kind" text, "unit" text, "coefficient_nominal" double precision, "coefficient_low" double precision, "coefficient_high" double precision, "coefficient_source" text, "coefficient_revision" text, "evidence_ref" text, "valid_from" timestamp with time zone, "valid_to" timestamp with time zone, "has_uncertainty" boolean, "alternative_revisions" jsonb) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "greenhouse_id", "equipment_id", "equipment_slug", "equipment_kind", "equipment_name", "resource_kind", "unit", "coefficient_nominal", "coefficient_low", "coefficient_high", "coefficient_source", "coefficient_revision", "evidence_ref", "valid_from", "valid_to", "has_uncertainty", "alternative_revisions" FROM public.v_equipment_resource_catalog $read$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_equipment_resource_catalog() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_equipment_resource_catalog() TO verdify_lab_publisher_runtime;

CREATE VIEW verdify_lab_publisher_runtime.v_equipment_resource_catalog WITH(security_barrier=true) AS SELECT * FROM verdify_lab_publisher_runtime.fn_read_v_equipment_resource_catalog();

REVOKE ALL ON verdify_lab_publisher_runtime.v_equipment_resource_catalog FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.v_equipment_resource_catalog TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_read_v_forecast_plan_outcome_mart() RETURNS TABLE("plan_id" text, "date" date, "created_at" timestamp with time zone, "planner_instance" text, "trigger_id" uuid, "temp_mae_f" numeric, "vpd_mae_kpa" numeric, "solar_mae_w" numeric, "avg_tunables" jsonb, "compliance_pct" double precision, "temp_compliance_pct" double precision, "vpd_compliance_pct" double precision, "stress_hours_heat" double precision, "stress_hours_vpd_high" double precision, "stress_hours_cold" double precision, "stress_hours_vpd_low" double precision, "water_used_gal" double precision, "mister_water_gal" double precision, "kwh" double precision, "therms_estimated" double precision, "cost_total" double precision, "hypothesis" text, "experiment" text, "expected_outcome" text, "actual_outcome" text, "outcome_score" smallint, "validated_at" timestamp with time zone) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "plan_id", "date", "created_at", "planner_instance", "trigger_id", "temp_mae_f", "vpd_mae_kpa", "solar_mae_w", "avg_tunables", "compliance_pct", "temp_compliance_pct", "vpd_compliance_pct", "stress_hours_heat", "stress_hours_vpd_high", "stress_hours_cold", "stress_hours_vpd_low", "water_used_gal", "mister_water_gal", "kwh", "therms_estimated", "cost_total", "hypothesis", "experiment", "expected_outcome", "actual_outcome", "outcome_score", "validated_at" FROM public.v_forecast_plan_outcome_mart $read$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_forecast_plan_outcome_mart() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_forecast_plan_outcome_mart() TO verdify_lab_publisher_runtime;

CREATE VIEW verdify_lab_publisher_runtime.v_forecast_plan_outcome_mart WITH(security_barrier=true) AS SELECT * FROM verdify_lab_publisher_runtime.fn_read_v_forecast_plan_outcome_mart();

REVOKE ALL ON verdify_lab_publisher_runtime.v_forecast_plan_outcome_mart FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.v_forecast_plan_outcome_mart TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_read_v_forecast_verification_contract() RETURNS TABLE("verification_contract_version" integer, "vintage_basis" text, "instant_truth_basis" text, "solar_truth_basis" text, "indoor_response_verified" boolean) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "verification_contract_version", "vintage_basis", "instant_truth_basis", "solar_truth_basis", "indoor_response_verified" FROM public.v_forecast_verification_contract $read$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_forecast_verification_contract() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_forecast_verification_contract() TO verdify_lab_publisher_runtime;

CREATE VIEW verdify_lab_publisher_runtime.v_forecast_verification_contract WITH(security_barrier=true) AS SELECT * FROM verdify_lab_publisher_runtime.fn_read_v_forecast_verification_contract();

REVOKE ALL ON verdify_lab_publisher_runtime.v_forecast_verification_contract FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.v_forecast_verification_contract TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_read_v_mister_effectiveness() RETURNS TABLE("on_ts" timestamp with time zone, "equipment" text, "duration_s" integer, "vpd_before" numeric, "vpd_after" numeric, "vpd_delta" numeric, "outdoor_temp_f" numeric, "outdoor_rh_pct" numeric) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "on_ts", "equipment", "duration_s", "vpd_before", "vpd_after", "vpd_delta", "outdoor_temp_f", "outdoor_rh_pct" FROM public.v_mister_effectiveness $read$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_mister_effectiveness() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_mister_effectiveness() TO verdify_lab_publisher_runtime;

CREATE VIEW verdify_lab_publisher_runtime.v_mister_effectiveness WITH(security_barrier=true) AS SELECT * FROM verdify_lab_publisher_runtime.fn_read_v_mister_effectiveness();

REVOKE ALL ON verdify_lab_publisher_runtime.v_mister_effectiveness FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.v_mister_effectiveness TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_read_v_position_current() RETURNS TABLE("position_id" integer, "greenhouse_id" text, "position_label" text, "shelf_slug" text, "shelf_kind" text, "zone_id" integer, "zone_slug" text, "zone_name" text, "crop_id" integer, "crop_name" text, "crop_variety" text, "crop_stage" text, "crop_planted_date" date, "crop_expected_harvest" date, "crop_catalog_slug" text, "crop_days_in_place" integer, "is_occupied" boolean) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "position_id", "greenhouse_id", "position_label", "shelf_slug", "shelf_kind", "zone_id", "zone_slug", "zone_name", "crop_id", "crop_name", "crop_variety", "crop_stage", "crop_planted_date", "crop_expected_harvest", "crop_catalog_slug", "crop_days_in_place", "is_occupied" FROM public.v_position_current $read$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_position_current() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_position_current() TO verdify_lab_publisher_runtime;

CREATE VIEW verdify_lab_publisher_runtime.v_position_current WITH(security_barrier=true) AS SELECT * FROM verdify_lab_publisher_runtime.fn_read_v_position_current();

REVOKE ALL ON verdify_lab_publisher_runtime.v_position_current FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.v_position_current TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_read_v_zone_full() RETURNS TABLE("zone_id" integer, "greenhouse_id" text, "zone_slug" text, "zone_name" text, "orientation" text, "sensor_modbus_addr" integer, "peak_temp_f" double precision, "zone_status" text, "zone_notes" text, "shelves" jsonb, "sensors" jsonb, "equipment" jsonb, "water_systems" jsonb, "active_crops_fk_count" integer) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "zone_id", "greenhouse_id", "zone_slug", "zone_name", "orientation", "sensor_modbus_addr", "peak_temp_f", "zone_status", "zone_notes", "shelves", "sensors", "equipment", "water_systems", "active_crops_fk_count" FROM public.v_zone_full $read$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_zone_full() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_read_v_zone_full() TO verdify_lab_publisher_runtime;

CREATE VIEW verdify_lab_publisher_runtime.v_zone_full WITH(security_barrier=true) AS SELECT * FROM verdify_lab_publisher_runtime.fn_read_v_zone_full();

REVOKE ALL ON verdify_lab_publisher_runtime.v_zone_full FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.v_zone_full TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.verdify_embeddings WITH(security_barrier=true) AS SELECT "id", "source_type", "source_id", "chunk_idx", "content", "content_hash", "embedding", "metadata", "embedded_at" FROM public.verdify_embeddings;

REVOKE ALL ON verdify_lab_publisher_runtime.verdify_embeddings FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.verdify_embeddings TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.weather_forecast WITH(security_barrier=true) AS SELECT "ts", "fetched_at", "temp_f", "rh_pct", "wind_speed_mph", "wind_dir_deg", "cloud_cover_pct", "precip_prob_pct", "solar_w_m2", "dew_point_f", "feels_like_f", "vpd_kpa", "precip_in", "rain_in", "snow_in", "wind_gust_mph", "uv_index", "et0_mm", "direct_radiation_w_m2", "diffuse_radiation_w_m2", "sunshine_duration_s", "weather_code", "cloud_cover_low_pct", "cloud_cover_high_pct", "surface_pressure_hpa", "soil_temp_f", "visibility_m", "greenhouse_id" FROM public.weather_forecast;

REVOKE ALL ON verdify_lab_publisher_runtime.weather_forecast FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.weather_forecast TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_lab_publisher_runtime.zones WITH(security_barrier=true) AS SELECT "id", "greenhouse_id", "slug", "name", "orientation", "sensor_modbus_addr", "peak_temp_f", "status", "notes", "created_at" FROM public.zones;

REVOKE ALL ON verdify_lab_publisher_runtime.zones FROM PUBLIC;

GRANT SELECT ON verdify_lab_publisher_runtime.zones TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_setpoint_at(p_param text, p_ts timestamp with time zone) RETURNS double precision LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT public.fn_setpoint_at($1,$2) $call$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_setpoint_at(p_param text, p_ts timestamp with time zone) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_setpoint_at(p_param text, p_ts timestamp with time zone) TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_setpoint_at(p_greenhouse_id text, p_param text, p_ts timestamp with time zone) RETURNS double precision LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT public.fn_setpoint_at($1,$2,$3) $call$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_setpoint_at(p_greenhouse_id text, p_param text, p_ts timestamp with time zone) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_setpoint_at(p_greenhouse_id text, p_param text, p_ts timestamp with time zone) TO verdify_lab_publisher_runtime;

CREATE FUNCTION verdify_lab_publisher_runtime.fn_forecast_correction(param text, lead_hours_max numeric DEFAULT 24) RETURNS TABLE(parameter text, avg_error numeric, samples bigint) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT * FROM public.fn_forecast_correction($1,$2) $call$;

REVOKE ALL ON FUNCTION verdify_lab_publisher_runtime.fn_forecast_correction(param text, lead_hours_max numeric) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_lab_publisher_runtime.fn_forecast_correction(param text, lead_hours_max numeric) TO verdify_lab_publisher_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.alert_log WITH(security_barrier=true) AS SELECT "id", "ts", "alert_type", "severity", "sensor_id", "zone", "message", "details", "source", "disposition", "acknowledged_at", "acknowledged_by", "resolved_at", "resolved_by", "resolution", "slack_ts", "created_at", "updated_at", "category", "metric_value", "threshold_value", "notes", "greenhouse_id", "zone_id", "slack_channel_id", "slack_message_ts", "slack_thread_ts", "slack_last_posted_at", "slack_snoozed_until", "slack_snoozed_by", "slack_assigned_to" FROM public.alert_log;

REVOKE ALL ON verdify_grafana_runtime.alert_log FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.alert_log TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.climate WITH(security_barrier=true) AS SELECT "ts", "temp_avg", "temp_north", "temp_south", "temp_east", "temp_west", "temp_case", "temp_control", "temp_intake", "rh_avg", "rh_north", "rh_south", "rh_east", "rh_west", "rh_case", "vpd_avg", "vpd_north", "vpd_south", "vpd_east", "vpd_west", "vpd_control", "dew_point", "abs_humidity", "enthalpy_delta", "co2_ppm", "lux", "dli_today", "flow_gpm", "water_total_gal", "mister_water_today", "outdoor_temp_f", "outdoor_rh_pct", "ph_input", "ec_input", "ph_runoff_wall", "ec_runoff_wall", "ph_runoff_center", "ec_runoff_center", "moisture_north", "moisture_south", "moisture_center", "ppfd", "dli_par_today", "pressure_hpa", "leaf_temp_north", "leaf_temp_south", "leaf_wetness_north", "leaf_wetness_south", "wind_speed_mph", "wind_direction_deg", "outdoor_lux", "solar_irradiance_w_m2", "precip_in", "uv_index", "hydro_tds_ppm", "hydro_water_temp_f", "wind_gust_mph", "wind_lull_mph", "wind_speed_avg_mph", "wind_direction_avg_deg", "feels_like_f", "wet_bulb_temp_f", "vapor_pressure_inhg", "air_density_kg_m3", "precip_intensity_in_h", "lightning_count", "lightning_avg_dist_mi", "solar_altitude_deg", "solar_azimuth_deg", "hydro_ec_us_cm", "hydro_orp_mv", "hydro_ph", "hydro_battery_pct", "soil_moisture_south_1", "soil_temp_south_1", "soil_ec_south_1", "soil_moisture_south_2", "soil_temp_south_2", "soil_moisture_west", "soil_temp_west", "intake_rh", "intake_vpd", "outdoor_illuminance", "greenhouse_id", "solar_phase", "solar_sunrise_min", "solar_noon_min", "solar_sunset_min", "house_temp_target_f", "house_temp_delta_f", "house_vpd_target", "house_vpd_delta", "vpd_target_center", "vpd_target_south", "vpd_target_west", "vpd_target_east", "vpd_delta_center", "vpd_delta_south", "vpd_delta_west", "vpd_delta_east" FROM public.climate;

REVOKE ALL ON verdify_grafana_runtime.climate FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.climate TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.climate_action_log WITH(security_barrier=true) AS SELECT "ts", "greenhouse_id", "climate_action", "priority_axis", "temp_low_f", "temp_target_f", "temp_high_f", "vpd_low_kpa", "vpd_target_kpa", "vpd_high_kpa", "temp_target_delta_f", "vpd_target_delta_kpa", "temp_band_error_f", "vpd_band_error_kpa", "moisture_assist_state", "moisture_zone", "wet_assist_allowed", "wet_assist_block_reason", "fog_allowed", "fog_block_reason", "relay_truth", "resource_cost_estimate", "climate_intent_version", "plan_id", "trigger_id", "planner_instance", "sensor_status", "candidate_summary", "source_system_state", "policy_vector_id", "policy_generation", "policy_activation_sha256" FROM public.climate_action_log;

REVOKE ALL ON verdify_grafana_runtime.climate_action_log FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.climate_action_log TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.daily_summary WITH(security_barrier=true) AS SELECT "date", "cycles_fan1", "cycles_fan2", "cycles_heat1", "cycles_heat2", "cycles_fog", "cycles_vent", "cycles_dehum", "cycles_safety_dehum", "runtime_fan1_min", "runtime_fan2_min", "runtime_heat1_min", "runtime_heat2_min", "runtime_fog_min", "runtime_vent_min", "runtime_mister_south_h", "runtime_mister_west_h", "runtime_mister_center_h", "water_used_gal", "mister_water_gal", "dli_final", "captured_at", "kwh_total", "kwh_heat", "kwh_fans", "kwh_other", "peak_kw", "gas_used_therms", "runtime_grow_light_min", "cycles_grow_light", "runtime_drip_wall_h", "runtime_drip_center_h", "kwh_estimated", "therms_estimated", "cost_electric", "cost_gas", "cost_water", "cost_total", "temp_min", "temp_max", "temp_avg", "rh_min", "rh_max", "rh_avg", "vpd_min", "vpd_max", "vpd_avg", "co2_avg", "outdoor_temp_min", "outdoor_temp_max", "stress_hours_heat", "stress_hours_cold", "stress_hours_vpd_high", "stress_hours_vpd_low", "notes", "greenhouse_id", "min_dp_margin_f", "dp_risk_hours", "compliance_pct", "temp_compliance_pct", "vpd_compliance_pct", "mister_fairness_overrides_today", "cycles_mister_south", "cycles_mister_west", "cycles_mister_center", "cycles_drip_wall", "cycles_drip_center", "runtime_drip_wall_fert_h", "runtime_drip_center_fert_h", "runtime_mister_south_fert_h", "runtime_mister_west_fert_h", "runtime_fert_master_h", "runtime_irrigation_clean_h", "runtime_irrigation_fert_h", "runtime_irrigation_total_h", "cycles_drip_wall_fert", "cycles_drip_center_fert", "cycles_mister_south_fert", "cycles_mister_west_fert", "cycles_fert_master", "irrigation_water_gal", "fertigation_water_gal", "compliance_v2_raw_pct", "compliance_v2_attributable_pct", "compliance_v2_unachievable_frac", "graded_temp_compliance_pct", "graded_vpd_compliance_pct", "graded_stress_hours_heat", "graded_stress_hours_cold", "graded_stress_hours_vpd_high", "graded_stress_hours_vpd_low", "feasibility_unknown_min", "dev_temp_norm_median_day", "dev_temp_norm_median_night", "dev_temp_norm_p95", "dev_vpd_norm_median_day", "dev_vpd_norm_median_night", "dev_vpd_norm_p95", "runtime_grow_light_main_min", "runtime_grow_light_grow_min", "climate_observed_minute_metrics" FROM public.daily_summary;

REVOKE ALL ON verdify_grafana_runtime.daily_summary FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.daily_summary TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.diagnostics WITH(security_barrier=true) AS SELECT "ts", "wifi_rssi", "heap_bytes", "uptime_s", "probe_health", "reset_reason", "greenhouse_id", "firmware_version", "active_probe_count", "relief_cycle_count", "vent_latch_timer_s", "sealed_timer_s", "vpd_watch_timer_s", "mist_backoff_timer_s", "vent_mist_assist_active", "heap_min_free_kb", "heap_largest_free_block_kb", "controller_time_epoch", "controller_local_hour", "sntp_valid", "sntp_miss_count", "last_sntp_sync_age_s", "effective_heat_target_f", "effective_cool_stage2_delta_f", "effective_vpd_hysteresis_kpa", "effective_dehum_aggressive_kpa", "zone_wet_granted", "band_source" FROM public.diagnostics;

REVOKE ALL ON verdify_grafana_runtime.diagnostics FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.diagnostics TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.energy WITH(security_barrier=true) AS SELECT "ts", "watts_total", "watts_heat", "watts_fans", "watts_other", "kwh_today", "greenhouse_id", "measurement_revision", "ch0_power_w", "ch1_power_w", "ch0_source_ts", "ch1_source_ts", "ch0_entity_id", "ch1_entity_id", "ch0_quality", "ch1_quality" FROM public.energy;

REVOKE ALL ON verdify_grafana_runtime.energy FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.energy TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.equipment_state WITH(security_barrier=true) AS SELECT "ts", "equipment", "state", "greenhouse_id" FROM public.equipment_state;

REVOKE ALL ON verdify_grafana_runtime.equipment_state FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.equipment_state TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.gpu_power WITH(security_barrier=true) AS SELECT "ts", "host", "gpu", "device", "model_name", "watts", "source", "raw", "greenhouse_id", "vm_name", "purpose", "gpu_util_pct", "temperature_c", "memory_used_mb", "memory_free_mb" FROM public.gpu_power;

REVOKE ALL ON verdify_grafana_runtime.gpu_power FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.gpu_power TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.infra_cpu WITH(security_barrier=true) AS SELECT "ts", "host", "vm_name", "purpose", "cpu_util_pct", "load1", "cores", "memory_used_pct", "source", "raw", "greenhouse_id" FROM public.infra_cpu;

REVOKE ALL ON verdify_grafana_runtime.infra_cpu FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.infra_cpu TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.instrumentation_requirements WITH(security_barrier=true) AS SELECT "requirement_id", "category", "metric", "target_table", "target_column", "current_status", "blocks_story", "recommended_source", "priority", "created_at", "updated_at" FROM public.instrumentation_requirements;

REVOKE ALL ON verdify_grafana_runtime.instrumentation_requirements FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.instrumentation_requirements TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.maintenance_log WITH(security_barrier=true) AS SELECT "id", "ts", "equipment", "service_type", "description", "cost", "technician", "next_due", "notes", "greenhouse_id" FROM public.maintenance_log;

REVOKE ALL ON verdify_grafana_runtime.maintenance_log FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.maintenance_log TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.mv_daily_kpi WITH(security_barrier=true) AS SELECT "date", "compliance_pct", "temp_compliance_pct", "vpd_compliance_pct", "heat_stress_h", "cold_stress_h", "vpd_high_stress_h", "vpd_low_stress_h", "total_stress_h", "kwh", "therms", "water_gal", "mister_water_gal", "cost_electric", "cost_gas", "cost_water", "cost_total", "temp_min", "temp_max", "temp_avg", "vpd_min", "vpd_max", "vpd_avg", "dli", "dp_margin_min_f", "dp_risk_hours", "planner_score", "planner_score_resource_weight_pct", "resource_terms_available", "dli_availability", "dli_unavailable_reason", "dli_provenance", "dli_validity_revision", "dli_valid_from", "dli_valid_to", "kwh_modeled", "therms_modeled", "water_gal_est", "cost_electric_est", "cost_gas_est", "cost_water_est", "cost_total_est" FROM public.mv_daily_kpi;

REVOKE ALL ON verdify_grafana_runtime.mv_daily_kpi FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.mv_daily_kpi TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.mv_equipment_runtime_daily WITH(security_barrier=true) AS SELECT "day", "equipment", "on_minutes", "cycles", "greenhouse_id", "day_started_at", "observed_through", "is_complete_day", "start_state_known", "start_state", "end_state", "open_at_end", "is_deploy_gate_eligible", "quality", "quality_flags", "starts", "cycles_under_1m", "cycles_1m_to_5m", "short_cycles_under_5m", "cycles_5m_to_15m", "cycles_15m_plus", "open_pulses_at_cutoff", "peak_transitions_per_hour", "raw_event_rows", "normalized_transition_count", "same_timestamp_duplicate_rows", "redundant_state_rows", "conflicting_timestamp_count", "starts_with_unknown_prior_state" FROM public.mv_equipment_runtime_daily;

REVOKE ALL ON verdify_grafana_runtime.mv_equipment_runtime_daily FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.mv_equipment_runtime_daily TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.plan_delivery_log WITH(security_barrier=true) AS SELECT "id", "delivered_at", "event_type", "event_label", "session_key", "wake_mode", "gateway_status", "gateway_body", "resulting_plan_id", "plan_written_at", "greenhouse_id", "trigger_id", "instance", "acked_at", "status", "hermes_run_id", "terminal_action", "terminal_at", "failure_class", "result_payload" FROM public.plan_delivery_log;

REVOKE ALL ON verdify_grafana_runtime.plan_delivery_log FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.plan_delivery_log TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.plan_journal WITH(security_barrier=true) AS SELECT "plan_id", "created_at", "conditions_summary", "hypothesis", "experiment", "expected_outcome", "params_changed", "actual_outcome", "outcome_score", "lesson_extracted", "validated_at", "greenhouse_id", "hypothesis_structured", "planner_instance", "trigger_id", "anchor_score", "climate_intents", "climate_intent_version", "guardrail_penalty", "valid_from", "expires_at", "lifecycle_status" FROM public.plan_journal;

REVOKE ALL ON verdify_grafana_runtime.plan_journal FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.plan_journal TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.sensor_registry WITH(security_barrier=true) AS SELECT "sensor_id", "entity_id", "type", "zone", "position", "source_table", "source_column", "unit", "expected_interval_s", "active", "notes", "created_at", "description", "installed_date", "updated_at" FROM public.sensor_registry;

REVOKE ALL ON verdify_grafana_runtime.sensor_registry FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.sensor_registry TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.setpoint_changes WITH(security_barrier=true) AS SELECT "ts", "parameter", "value", "source", "greenhouse_id", "confirmed_at", "planner_instance", "trigger_id", "delivery_status", "expired_at", "superseded_by_ts", "zone" FROM public.setpoint_changes;

REVOKE ALL ON verdify_grafana_runtime.setpoint_changes FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.setpoint_changes TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.setpoint_plan WITH(security_barrier=true) AS SELECT "ts", "parameter", "value", "plan_id", "source", "reason", "created_at", "is_active", "greenhouse_id", "trigger_id", "planner_instance", "expires_at" FROM public.setpoint_plan;

REVOKE ALL ON verdify_grafana_runtime.setpoint_plan FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.setpoint_plan TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.setpoint_snapshot WITH(security_barrier=true) AS SELECT "ts", "parameter", "value", "greenhouse_id", "zone", "band_role", "target_value" FROM public.setpoint_snapshot;

REVOKE ALL ON verdify_grafana_runtime.setpoint_snapshot FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.setpoint_snapshot TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.system_state WITH(security_barrier=true) AS SELECT "ts", "entity", "value", "greenhouse_id" FROM public.system_state;

REVOKE ALL ON verdify_grafana_runtime.system_state FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.system_state TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_band_curve() RETURNS TABLE("ts" timestamp with time zone, "greenhouse_id" text, "temp_low" double precision, "temp_target" double precision, "temp_high" double precision, "vpd_low" double precision, "vpd_target" double precision, "vpd_high" double precision, "vpd_target_center" double precision, "vpd_target_south" double precision, "vpd_target_west" double precision, "vpd_target_east" double precision, "solar_phase" double precision) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "ts", "greenhouse_id", "temp_low", "temp_target", "temp_high", "vpd_low", "vpd_target", "vpd_high", "vpd_target_center", "vpd_target_south", "vpd_target_west", "vpd_target_east", "solar_phase" FROM public.v_band_curve $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_band_curve() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_band_curve() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_band_curve WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_band_curve();

REVOKE ALL ON verdify_grafana_runtime.v_band_curve FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_band_curve TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.v_climate_merged WITH(security_barrier=true) AS SELECT "bucket", "temp_avg", "temp_north", "temp_south", "rh_avg", "vpd_avg", "co2_ppm", "lux", "dli_today", "outdoor_temp_f", "outdoor_rh_pct", "solar_w_m2", "solar_alt", "solar_az", "wind_mph", "pressure_hpa", "outdoor_lux", "flow_gpm", "enthalpy_delta" FROM public.v_climate_merged;

REVOKE ALL ON verdify_grafana_runtime.v_climate_merged FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_climate_merged TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_dif() RETURNS TABLE("date" timestamp without time zone, "day_avg_temp" numeric, "night_avg_temp" numeric, "dif" numeric) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "date", "day_avg_temp", "night_avg_temp", "dif" FROM public.v_dif $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_dif() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_dif() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_dif WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_dif();

REVOKE ALL ON verdify_grafana_runtime.v_dif FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_dif TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_disease_risk() RETURNS TABLE("hour" timestamp with time zone, "botrytis_risk_pct" numeric, "condensation_risk_pct" numeric, "botrytis_consecutive_hours" numeric, "condensation_consecutive_hours" numeric) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "hour", "botrytis_risk_pct", "condensation_risk_pct", "botrytis_consecutive_hours", "condensation_consecutive_hours" FROM public.v_disease_risk $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_disease_risk() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_disease_risk() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_disease_risk WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_disease_risk();

REVOKE ALL ON verdify_grafana_runtime.v_disease_risk FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_disease_risk TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_dli_current() RETURNS TABLE("ts" timestamp with time zone, "greenhouse_id" text, "crop_dli_mol_m2_day" double precision, "availability" text, "unavailable_reason" text, "provenance" text, "validity_revision" text, "valid_from" timestamp with time zone, "valid_to" timestamp with time zone, "forensic_proxy_present" boolean) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "ts", "greenhouse_id", "crop_dli_mol_m2_day", "availability", "unavailable_reason", "provenance", "validity_revision", "valid_from", "valid_to", "forensic_proxy_present" FROM public.v_dli_current $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_dli_current() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_dli_current() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_dli_current WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_dli_current();

REVOKE ALL ON verdify_grafana_runtime.v_dli_current FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_dli_current TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_equipment_resource_catalog() RETURNS TABLE("greenhouse_id" text, "equipment_id" integer, "equipment_slug" text, "equipment_kind" text, "equipment_name" text, "resource_kind" text, "unit" text, "coefficient_nominal" double precision, "coefficient_low" double precision, "coefficient_high" double precision, "coefficient_source" text, "coefficient_revision" text, "evidence_ref" text, "valid_from" timestamp with time zone, "valid_to" timestamp with time zone, "has_uncertainty" boolean, "alternative_revisions" jsonb) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "greenhouse_id", "equipment_id", "equipment_slug", "equipment_kind", "equipment_name", "resource_kind", "unit", "coefficient_nominal", "coefficient_low", "coefficient_high", "coefficient_source", "coefficient_revision", "evidence_ref", "valid_from", "valid_to", "has_uncertainty", "alternative_revisions" FROM public.v_equipment_resource_catalog $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_equipment_resource_catalog() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_equipment_resource_catalog() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_equipment_resource_catalog WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_equipment_resource_catalog();

REVOKE ALL ON verdify_grafana_runtime.v_equipment_resource_catalog FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_equipment_resource_catalog TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_forecast_plan_outcome_mart() RETURNS TABLE("plan_id" text, "date" date, "created_at" timestamp with time zone, "planner_instance" text, "trigger_id" uuid, "temp_mae_f" numeric, "vpd_mae_kpa" numeric, "solar_mae_w" numeric, "avg_tunables" jsonb, "compliance_pct" double precision, "temp_compliance_pct" double precision, "vpd_compliance_pct" double precision, "stress_hours_heat" double precision, "stress_hours_vpd_high" double precision, "stress_hours_cold" double precision, "stress_hours_vpd_low" double precision, "water_used_gal" double precision, "mister_water_gal" double precision, "kwh" double precision, "therms_estimated" double precision, "cost_total" double precision, "hypothesis" text, "experiment" text, "expected_outcome" text, "actual_outcome" text, "outcome_score" smallint, "validated_at" timestamp with time zone) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "plan_id", "date", "created_at", "planner_instance", "trigger_id", "temp_mae_f", "vpd_mae_kpa", "solar_mae_w", "avg_tunables", "compliance_pct", "temp_compliance_pct", "vpd_compliance_pct", "stress_hours_heat", "stress_hours_vpd_high", "stress_hours_cold", "stress_hours_vpd_low", "water_used_gal", "mister_water_gal", "kwh", "therms_estimated", "cost_total", "hypothesis", "experiment", "expected_outcome", "actual_outcome", "outcome_score", "validated_at" FROM public.v_forecast_plan_outcome_mart $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_forecast_plan_outcome_mart() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_forecast_plan_outcome_mart() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_forecast_plan_outcome_mart WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_forecast_plan_outcome_mart();

REVOKE ALL ON verdify_grafana_runtime.v_forecast_plan_outcome_mart FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_forecast_plan_outcome_mart TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_gpu_power_latest() RETURNS TABLE("ts" timestamp with time zone, "host" text, "vm_name" text, "purpose" text, "gpu" text, "device" text, "model_name" text, "watts" double precision, "gpu_util_pct" double precision, "temperature_c" double precision, "memory_used_mb" double precision, "memory_free_mb" double precision, "source" text, "raw" jsonb, "greenhouse_id" text, "age_s" integer) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "ts", "host", "vm_name", "purpose", "gpu", "device", "model_name", "watts", "gpu_util_pct", "temperature_c", "memory_used_mb", "memory_free_mb", "source", "raw", "greenhouse_id", "age_s" FROM public.v_gpu_power_latest $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_gpu_power_latest() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_gpu_power_latest() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_gpu_power_latest WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_gpu_power_latest();

REVOKE ALL ON verdify_grafana_runtime.v_gpu_power_latest FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_gpu_power_latest TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_greenhouse_now() RETURNS TABLE("ts" timestamp with time zone, "temp_avg" numeric, "temp_north" numeric, "temp_south" numeric, "temp_east" numeric, "temp_west" numeric, "rh_avg" numeric, "vpd_avg" numeric, "co2_ppm" numeric, "lux" numeric, "dli_today" numeric, "outdoor_temp_f" numeric, "outdoor_rh_pct" numeric, "wind_mph" numeric, "pressure_hpa" numeric, "mister_water_today" numeric, "hydro_ph" numeric, "hydro_ec_us_cm" numeric, "hydro_tds_ppm" numeric, "hydro_water_temp_f" numeric, "wifi_rssi" double precision, "heap_kb" numeric, "uptime_s" numeric, "state" text, "lead_fan" text, "health_score" integer, "open_alerts" bigint, "cost_electric" numeric, "cost_gas" numeric, "cost_water" numeric, "cost_total" numeric, "dli_availability" text, "dli_unavailable_reason" text, "dli_provenance" text, "dli_validity_revision" text, "dli_valid_from" timestamp with time zone, "dli_valid_to" timestamp with time zone) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "ts", "temp_avg", "temp_north", "temp_south", "temp_east", "temp_west", "rh_avg", "vpd_avg", "co2_ppm", "lux", "dli_today", "outdoor_temp_f", "outdoor_rh_pct", "wind_mph", "pressure_hpa", "mister_water_today", "hydro_ph", "hydro_ec_us_cm", "hydro_tds_ppm", "hydro_water_temp_f", "wifi_rssi", "heap_kb", "uptime_s", "state", "lead_fan", "health_score", "open_alerts", "cost_electric", "cost_gas", "cost_water", "cost_total", "dli_availability", "dli_unavailable_reason", "dli_provenance", "dli_validity_revision", "dli_valid_from", "dli_valid_to" FROM public.v_greenhouse_now $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_greenhouse_now() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_greenhouse_now() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_greenhouse_now WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_greenhouse_now();

REVOKE ALL ON verdify_grafana_runtime.v_greenhouse_now FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_greenhouse_now TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_infra_cpu_latest() RETURNS TABLE("ts" timestamp with time zone, "host" text, "vm_name" text, "purpose" text, "cpu_util_pct" double precision, "load1" double precision, "cores" integer, "memory_used_pct" double precision, "source" text, "raw" jsonb, "greenhouse_id" text, "age_s" integer) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "ts", "host", "vm_name", "purpose", "cpu_util_pct", "load1", "cores", "memory_used_pct", "source", "raw", "greenhouse_id", "age_s" FROM public.v_infra_cpu_latest $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_infra_cpu_latest() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_infra_cpu_latest() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_infra_cpu_latest WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_infra_cpu_latest();

REVOKE ALL ON verdify_grafana_runtime.v_infra_cpu_latest FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_infra_cpu_latest TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_irrigation_fertigation_runs() RETURNS TABLE("run_id" text, "day" date, "zone_path" text, "serves_zones" text[], "schedule_id" text, "fert_relay" text, "flush_relay" text, "fert_start" timestamp with time zone, "fert_end" timestamp with time zone, "fert_duration_min" double precision, "flush_start" timestamp with time zone, "flush_end" timestamp with time zone, "flush_duration_min" double precision, "run_start" timestamp with time zone, "run_end" timestamp with time zone, "total_duration_min" double precision, "expected_fert_min" integer, "expected_flush_min" integer, "fert_master_overlap_min" double precision, "water_flowing_overlap_min" double precision, "meter_samples" integer, "min_total_gal" double precision, "max_total_gal" double precision, "meter_delta_gal" double precision, "avg_flow_gpm" double precision, "max_flow_gpm" double precision, "quality_flags" text[], "quality_flag" text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "run_id", "day", "zone_path", "serves_zones", "schedule_id", "fert_relay", "flush_relay", "fert_start", "fert_end", "fert_duration_min", "flush_start", "flush_end", "flush_duration_min", "run_start", "run_end", "total_duration_min", "expected_fert_min", "expected_flush_min", "fert_master_overlap_min", "water_flowing_overlap_min", "meter_samples", "min_total_gal", "max_total_gal", "meter_delta_gal", "avg_flow_gpm", "max_flow_gpm", "quality_flags", "quality_flag" FROM public.v_irrigation_fertigation_runs $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_irrigation_fertigation_runs() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_irrigation_fertigation_runs() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_irrigation_fertigation_runs WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_irrigation_fertigation_runs();

REVOKE ALL ON verdify_grafana_runtime.v_irrigation_fertigation_runs FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_irrigation_fertigation_runs TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_irrigation_program_daily() RETURNS TABLE("date" date, "fertigation_events" bigint, "runtime_min" double precision, "fert_runtime_min" double precision, "flush_runtime_min" double precision, "fert_master_overlap_min" double precision, "meter_delta_gal" double precision, "flagged_events" bigint, "latest_event" timestamp with time zone) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "date", "fertigation_events", "runtime_min", "fert_runtime_min", "flush_runtime_min", "fert_master_overlap_min", "meter_delta_gal", "flagged_events", "latest_event" FROM public.v_irrigation_program_daily $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_irrigation_program_daily() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_irrigation_program_daily() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_irrigation_program_daily WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_irrigation_program_daily();

REVOKE ALL ON verdify_grafana_runtime.v_irrigation_program_daily FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_irrigation_program_daily TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_irrigation_schedule_current() RETURNS TABLE("schedule_id" text, "zone_path" text, "display_name" text, "serves_zones" text[], "enabled" boolean, "start_time" time without time zone, "clean_duration_min" integer, "fert_duration_min" integer, "flush_min" integer, "interval_days" integer, "days_mask" integer, "fert_days_mask" integer, "fert_every_n" integer, "fertigation_enabled" boolean, "fert_relays" text[], "flush_relays" text[], "schedule_source" text, "plan_id" text, "plan_ts" timestamp with time zone, "stale_readback_count" bigint, "readback_drift_count" bigint, "notes" text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "schedule_id", "zone_path", "display_name", "serves_zones", "enabled", "start_time", "clean_duration_min", "fert_duration_min", "flush_min", "interval_days", "days_mask", "fert_days_mask", "fert_every_n", "fertigation_enabled", "fert_relays", "flush_relays", "schedule_source", "plan_id", "plan_ts", "stale_readback_count", "readback_drift_count", "notes" FROM public.v_irrigation_schedule_current $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_irrigation_schedule_current() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_irrigation_schedule_current() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_irrigation_schedule_current WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_irrigation_schedule_current();

REVOKE ALL ON verdify_grafana_runtime.v_irrigation_schedule_current FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_irrigation_schedule_current TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_irrigation_sensor_feedback_status() RETURNS TABLE("feedback_key" text, "zone" text, "signal" text, "last_sample_ts" timestamp with time zone, "latest_value" double precision, "status" text, "details" jsonb, "required_action" text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "feedback_key", "zone", "signal", "last_sample_ts", "latest_value", "status", "details", "required_action" FROM public.v_irrigation_sensor_feedback_status $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_irrigation_sensor_feedback_status() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_irrigation_sensor_feedback_status() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_irrigation_sensor_feedback_status WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_irrigation_sensor_feedback_status();

REVOKE ALL ON verdify_grafana_runtime.v_irrigation_sensor_feedback_status FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_irrigation_sensor_feedback_status TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_lighting_traceability_now() RETURNS TABLE("greenhouse_id" text, "ts" timestamp with time zone, "light_key" text, "equipment" text, "target_light_minutes" integer, "start_hour" integer, "cutoff_hour" integer, "lux_on_threshold" double precision, "lux_hysteresis" double precision, "lux_off_threshold" double precision, "min_on_s" integer, "min_off_s" integer, "auto_enabled" boolean, "legacy_dli_target" double precision, "source_chain" text, "controller_contract" text, "qualified_light_minutes" integer, "natural_qualified_minutes" integer, "switch_on_minutes" integer, "overlap_minutes" integer, "remaining_light_minutes" integer, "climate_ts" timestamp with time zone, "dli_today" double precision, "indoor_lux" double precision, "outdoor_lux" double precision, "exterior_lux" double precision, "natural_lux" double precision, "natural_qualified_now" boolean, "local_hour" integer, "in_light_window" boolean, "minutes_below_target" boolean, "lux_below_on_threshold" boolean, "lux_below_off_threshold" boolean, "exterior_lux_fresh" boolean, "exterior_lux_below_on_threshold" boolean, "exterior_lux_below_off_threshold" boolean, "occupancy_active" boolean, "actual_on" boolean, "firmware_state" text, "firmware_reason" text, "firmware_telemetry_fresh" boolean, "equipment_ts" timestamp with time zone, "plant_supplement_demand" boolean, "occupancy_lux_demand" boolean, "expected_on" boolean, "cfg_target_light_minutes" double precision, "cfg_lux_on_threshold" double precision, "cfg_lux_hysteresis" double precision, "cfg_auto_enabled" boolean, "cfg_auto_ts" timestamp with time zone, "desired_target_light_minutes" double precision, "desired_lux_on_threshold" double precision, "desired_lux_hysteresis" double precision, "desired_auto_enabled" boolean, "desired_auto_delivery_status" text, "desired_auto_ts" timestamp with time zone, "firmware_decision_epoch" bigint, "firmware_decision_ts" timestamp with time zone, "firmware_decision_fresh" boolean, "policy_matches_cfg" boolean, "dli_availability" text, "dli_unavailable_reason" text, "dli_provenance" text, "dli_validity_revision" text, "dli_valid_from" timestamp with time zone, "dli_valid_to" timestamp with time zone) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "greenhouse_id", "ts", "light_key", "equipment", "target_light_minutes", "start_hour", "cutoff_hour", "lux_on_threshold", "lux_hysteresis", "lux_off_threshold", "min_on_s", "min_off_s", "auto_enabled", "legacy_dli_target", "source_chain", "controller_contract", "qualified_light_minutes", "natural_qualified_minutes", "switch_on_minutes", "overlap_minutes", "remaining_light_minutes", "climate_ts", "dli_today", "indoor_lux", "outdoor_lux", "exterior_lux", "natural_lux", "natural_qualified_now", "local_hour", "in_light_window", "minutes_below_target", "lux_below_on_threshold", "lux_below_off_threshold", "exterior_lux_fresh", "exterior_lux_below_on_threshold", "exterior_lux_below_off_threshold", "occupancy_active", "actual_on", "firmware_state", "firmware_reason", "firmware_telemetry_fresh", "equipment_ts", "plant_supplement_demand", "occupancy_lux_demand", "expected_on", "cfg_target_light_minutes", "cfg_lux_on_threshold", "cfg_lux_hysteresis", "cfg_auto_enabled", "cfg_auto_ts", "desired_target_light_minutes", "desired_lux_on_threshold", "desired_lux_hysteresis", "desired_auto_enabled", "desired_auto_delivery_status", "desired_auto_ts", "firmware_decision_epoch", "firmware_decision_ts", "firmware_decision_fresh", "policy_matches_cfg", "dli_availability", "dli_unavailable_reason", "dli_provenance", "dli_validity_revision", "dli_valid_from", "dli_valid_to" FROM public.v_lighting_traceability_now $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_lighting_traceability_now() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_lighting_traceability_now() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_lighting_traceability_now WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_lighting_traceability_now();

REVOKE ALL ON verdify_grafana_runtime.v_lighting_traceability_now FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_lighting_traceability_now TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_mister_effectiveness() RETURNS TABLE("on_ts" timestamp with time zone, "equipment" text, "duration_s" integer, "vpd_before" numeric, "vpd_after" numeric, "vpd_delta" numeric, "outdoor_temp_f" numeric, "outdoor_rh_pct" numeric) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "on_ts", "equipment", "duration_s", "vpd_before", "vpd_after", "vpd_delta", "outdoor_temp_f", "outdoor_rh_pct" FROM public.v_mister_effectiveness $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_mister_effectiveness() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_mister_effectiveness() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_mister_effectiveness WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_mister_effectiveness();

REVOKE ALL ON verdify_grafana_runtime.v_mister_effectiveness FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_mister_effectiveness TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_plan_compliance() RETURNS TABLE("planned_ts" timestamp with time zone, "plan_id" text, "parameter" text, "target_type" text, "outcome_score" smallint, "anchor_score" smallint, "validated_at" timestamp with time zone, "plan_achieved" boolean, "overshoot" numeric, "accuracy_pct" numeric) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "planned_ts", "plan_id", "parameter", "target_type", "outcome_score", "anchor_score", "validated_at", "plan_achieved", "overshoot", "accuracy_pct" FROM public.v_plan_compliance $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_plan_compliance() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_plan_compliance() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_plan_compliance WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_plan_compliance();

REVOKE ALL ON verdify_grafana_runtime.v_plan_compliance FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_plan_compliance TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_planner_performance() RETURNS TABLE("date" date, "heat_stress_h" double precision, "cold_stress_h" double precision, "vpd_high_stress_h" double precision, "vpd_low_stress_h" double precision, "total_stress_h" double precision, "compliance_pct" numeric, "temp_compliance_pct" numeric, "vpd_compliance_pct" numeric, "cost_total" double precision, "cost_electric" double precision, "cost_gas" double precision, "cost_water" double precision, "cost_per_stress_hour" numeric, "planner_score" numeric, "compliance_binary_pct" numeric, "compliance_raw_graded_pct" numeric, "unachievable_frac" numeric, "planner_score_resource_weight_pct" numeric, "resource_terms_available" boolean) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "date", "heat_stress_h", "cold_stress_h", "vpd_high_stress_h", "vpd_low_stress_h", "total_stress_h", "compliance_pct", "temp_compliance_pct", "vpd_compliance_pct", "cost_total", "cost_electric", "cost_gas", "cost_water", "cost_per_stress_hour", "planner_score", "compliance_binary_pct", "compliance_raw_graded_pct", "unachievable_frac", "planner_score_resource_weight_pct", "resource_terms_available" FROM public.v_planner_performance $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_planner_performance() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_planner_performance() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_planner_performance WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_planner_performance();

REVOKE ALL ON verdify_grafana_runtime.v_planner_performance FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_planner_performance TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_reboot_log() RETURNS TABLE("ts" timestamp with time zone, "uptime_after" double precision, "reset_reason" text, "uptime_before" double precision, "gap_s" integer) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "ts", "uptime_after", "reset_reason", "uptime_before", "gap_s" FROM public.v_reboot_log $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_reboot_log() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_reboot_log() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_reboot_log WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_reboot_log();

REVOKE ALL ON verdify_grafana_runtime.v_reboot_log FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_reboot_log TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_runtime_energy_daily() RETURNS TABLE("date" date, "greenhouse_id" text, "modeled_kwh" double precision, "modeled_kwh_low" double precision, "modeled_kwh_high" double precision, "runtime_coverage_pct" double precision, "coefficient_revisions" jsonb, "modeled_scope" text, "model_quality" text, "available_for_scoring" boolean, "runtime_evidence" jsonb) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "date", "greenhouse_id", "modeled_kwh", "modeled_kwh_low", "modeled_kwh_high", "runtime_coverage_pct", "coefficient_revisions", "modeled_scope", "model_quality", "available_for_scoring", "runtime_evidence" FROM public.v_runtime_energy_daily $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_runtime_energy_daily() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_runtime_energy_daily() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_runtime_energy_daily WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_runtime_energy_daily();

REVOKE ALL ON verdify_grafana_runtime.v_runtime_energy_daily FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_runtime_energy_daily TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_setpoint_velocity() RETURNS TABLE("hour" timestamp with time zone, "parameter" text, "source" text, "writes" bigint, "oscillations" bigint) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "hour", "parameter", "source", "writes", "oscillations" FROM public.v_setpoint_velocity $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_setpoint_velocity() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_setpoint_velocity() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_setpoint_velocity WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_setpoint_velocity();

REVOKE ALL ON verdify_grafana_runtime.v_setpoint_velocity FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_setpoint_velocity TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_state_transition_rate() RETURNS TABLE("hour" timestamp with time zone, "transitions" bigint, "unique_states" bigint) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "hour", "transitions", "unique_states" FROM public.v_state_transition_rate $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_state_transition_rate() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_state_transition_rate() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_state_transition_rate WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_state_transition_rate();

REVOKE ALL ON verdify_grafana_runtime.v_state_transition_rate FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_state_transition_rate TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_system_health_score() RETURNS TABLE("component" text, "score_pct" numeric, "details" jsonb, "checked_at" timestamp with time zone) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "component", "score_pct", "details", "checked_at" FROM public.v_system_health_score $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_system_health_score() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_system_health_score() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_system_health_score WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_system_health_score();

REVOKE ALL ON verdify_grafana_runtime.v_system_health_score FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_system_health_score TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_read_v_water_attribution_daily() RETURNS TABLE("date" date, "greenhouse_id" text, "quality_filtered_meter_gal" double precision, "attributed_gal" double precision, "climate_wetting_gal" double precision, "wall_irrigation_gal" double precision, "wall_fertigation_gal" double precision, "unsupported_path_gal" double precision, "ambiguous_gal" double precision, "manual_or_unattributed_gal" double precision, "command_only_runs" bigint, "ambiguous_runs" bigint, "meter_attributed_runs" bigint, "conservation_error_gal" double precision, "ledger_quality" text, "resource_quality" text, "available_for_scoring" boolean) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $read$ SELECT "date", "greenhouse_id", "quality_filtered_meter_gal", "attributed_gal", "climate_wetting_gal", "wall_irrigation_gal", "wall_fertigation_gal", "unsupported_path_gal", "ambiguous_gal", "manual_or_unattributed_gal", "command_only_runs", "ambiguous_runs", "meter_attributed_runs", "conservation_error_gal", "ledger_quality", "resource_quality", "available_for_scoring" FROM public.v_water_attribution_daily $read$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_read_v_water_attribution_daily() FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_read_v_water_attribution_daily() TO verdify_grafana_runtime;

CREATE VIEW verdify_grafana_runtime.v_water_attribution_daily WITH(security_barrier=true) AS SELECT * FROM verdify_grafana_runtime.fn_read_v_water_attribution_daily();

REVOKE ALL ON verdify_grafana_runtime.v_water_attribution_daily FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.v_water_attribution_daily TO verdify_grafana_runtime;

-- Owner-held table projection preserves index/limit pushdown; no shared ACL grant.
CREATE VIEW verdify_grafana_runtime.weather_forecast WITH(security_barrier=true) AS SELECT "ts", "fetched_at", "temp_f", "rh_pct", "wind_speed_mph", "wind_dir_deg", "cloud_cover_pct", "precip_prob_pct", "solar_w_m2", "dew_point_f", "feels_like_f", "vpd_kpa", "precip_in", "rain_in", "snow_in", "wind_gust_mph", "uv_index", "et0_mm", "direct_radiation_w_m2", "diffuse_radiation_w_m2", "sunshine_duration_s", "weather_code", "cloud_cover_low_pct", "cloud_cover_high_pct", "surface_pressure_hpa", "soil_temp_f", "visibility_m", "greenhouse_id" FROM public.weather_forecast;

REVOKE ALL ON verdify_grafana_runtime.weather_forecast FROM PUBLIC;

GRANT SELECT ON verdify_grafana_runtime.weather_forecast TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_band_timeline(p_start timestamp with time zone, p_end timestamp with time zone, p_step interval DEFAULT '00:30:00'::interval, p_greenhouse_id text DEFAULT 'vallery'::text) RETURNS TABLE(ts timestamp with time zone, greenhouse_id text, timeline_phase text, crop_temp_low double precision, crop_temp_high double precision, crop_vpd_low double precision, crop_vpd_high double precision, projected_temp_low double precision, projected_temp_high double precision, projected_vpd_low double precision, projected_vpd_high double precision, actual_temp_low double precision, actual_temp_high double precision, actual_vpd_low double precision, actual_vpd_high double precision, firmware_temp_low double precision, firmware_temp_high double precision, firmware_vpd_low double precision, firmware_vpd_high double precision, temp_width_f double precision, vpd_width_kpa double precision, sw_fsm_controller_enabled boolean, indoor_temp_f double precision, indoor_vpd_kpa double precision, outdoor_temp_f double precision, outdoor_vpd_kpa double precision, solar_w_m2 double precision, outdoor_cold_for_vent boolean, temp_hysteresis_f double precision, heat_hysteresis_f double precision, d_heat_stage_2_f double precision, d_cool_stage_2_f double precision, bias_heat_f double precision, bias_cool_f double precision, vpd_hysteresis_kpa double precision, vpd_hysteresis_effective_kpa double precision, fog_escalation_kpa double precision, temp_heat_target_f double precision, temp_heat_on_below_f double precision, temp_heat2_on_below_f double precision, temp_heat2_clear_f double precision, temp_cool_on_above_f double precision, temp_cool_hold_until_f double precision, temp_cooling_entry_margin_f double precision, temp_cooling_exit_hysteresis_f double precision, solar_cooling_lead_f double precision, temp_cool_stage2_delta_f double precision, temp_cool_stage2_on_above_f double precision, vpd_humidify_on_above_kpa double precision, vpd_humidify_resolved_below_kpa double precision, vpd_dehum_on_below_kpa double precision, vpd_dehum_resolved_above_kpa double precision, vpd_low_eff_kpa double precision, vpd_high_eff_kpa double precision, vpd_vent_fog_on_above_kpa double precision, vpd_sealed_fog_on_above_kpa double precision) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT * FROM public.fn_band_timeline($1,$2,$3,$4) $call$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_band_timeline(p_start timestamp with time zone, p_end timestamp with time zone, p_step interval, p_greenhouse_id text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_band_timeline(p_start timestamp with time zone, p_end timestamp with time zone, p_step interval, p_greenhouse_id text) TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_lighting_timeline(p_start timestamp with time zone, p_end timestamp with time zone, p_step interval DEFAULT '00:30:00'::interval, p_greenhouse_id text DEFAULT 'vallery'::text) RETURNS TABLE(ts timestamp with time zone, natural_lux double precision, natural_lux_source text, main_lux_on_threshold double precision, main_lux_off_threshold double precision, grow_lux_on_threshold double precision, grow_lux_off_threshold double precision, main_expected_on double precision, grow_expected_on double precision, main_dli_target double precision, grow_dli_target double precision, main_target_light_minutes integer, grow_target_light_minutes integer, main_qualified_light_minutes double precision, grow_qualified_light_minutes double precision) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT * FROM public.fn_lighting_timeline($1,$2,$3,$4) $call$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_lighting_timeline(p_start timestamp with time zone, p_end timestamp with time zone, p_step interval, p_greenhouse_id text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_lighting_timeline(p_start timestamp with time zone, p_end timestamp with time zone, p_step interval, p_greenhouse_id text) TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_runtime_power_30m(p_start timestamp with time zone, p_end timestamp with time zone) RETURNS TABLE(bucket timestamp with time zone, total_watts double precision, heat1_watts double precision, fans_watts double precision, other_watts double precision) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT * FROM public.fn_runtime_power_30m($1,$2) $call$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_runtime_power_30m(p_start timestamp with time zone, p_end timestamp with time zone) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_runtime_power_30m(p_start timestamp with time zone, p_end timestamp with time zone) TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_lighting_policy(p_ts timestamp with time zone DEFAULT now(), p_greenhouse_id text DEFAULT 'vallery'::text) RETURNS TABLE(greenhouse_id text, ts timestamp with time zone, local_date date, target_dli double precision, target_ppfd_umol_m2_s double precision, target_light_hours integer, sunrise_hour integer, natural_sunset_hour integer, cutoff_hour integer, max_crop_name text, max_crop_stage text, source_chain text, controller_contract text) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT * FROM public.fn_lighting_policy($1,$2) $call$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_lighting_policy(p_ts timestamp with time zone, p_greenhouse_id text) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_lighting_policy(p_ts timestamp with time zone, p_greenhouse_id text) TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_forecast_correction(param text, lead_hours_max numeric DEFAULT 24) RETURNS TABLE(parameter text, avg_error numeric, samples bigint) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT * FROM public.fn_forecast_correction($1,$2) $call$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_forecast_correction(param text, lead_hours_max numeric) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_forecast_correction(param text, lead_hours_max numeric) TO verdify_grafana_runtime;

CREATE FUNCTION verdify_grafana_runtime.fn_planner_scorecard(p_date date DEFAULT CURRENT_DATE) RETURNS TABLE(metric text, value numeric) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $call$ SELECT * FROM public.fn_planner_scorecard($1) $call$;

REVOKE ALL ON FUNCTION verdify_grafana_runtime.fn_planner_scorecard(p_date date) FROM PUBLIC;

GRANT EXECUTE ON FUNCTION verdify_grafana_runtime.fn_planner_scorecard(p_date date) TO verdify_grafana_runtime;

-- #643 source-owned blinded projection candidate for reserved268.
-- NOT an applied/ledgered migration; no grants or credentials are installed here.
-- Namespace and runtime identity creation belongs to the complete six-role migration.
-- Preserve source panels without granting mapping/hash/control table access.

CREATE VIEW verdify_grafana_runtime.blinded_exposure_coverage
WITH (security_barrier=true, security_invoker=false) AS
SELECT started_at, ended_at, identity_confirmed
FROM public.policy_exposures;

CREATE VIEW verdify_grafana_runtime.blinded_activation_identity
WITH (security_barrier=true, security_invoker=false) AS
SELECT updated_at, observed_activation_sha256 IS NOT NULL AS has_observed_identity,
       observed_activation_sha256 = expected_activation_sha256 AS identity_ok
FROM public.policy_exposures;

CREATE VIEW verdify_grafana_runtime.blinded_activation_lineage
WITH (security_barrier=true, security_invoker=false) AS
SELECT a.assignment_id, lower(a.valid_range) AS starts_at,
       o.staged_at, o.activated_at, o.activated_at-o.created_at AS delivery_lag,
       e.identity_confirmed, e.coverage_fraction, e.close_reason
FROM public.control_assignments a
LEFT JOIN LATERAL (
    SELECT ob.staged_at, ob.activated_at, ob.created_at
    FROM public.policy_delivery_outbox ob
    JOIN public.effective_policy_vectors v ON v.vector_id=ob.vector_id
    WHERE v.assignment_id=a.assignment_id
    ORDER BY ob.created_at DESC LIMIT 1
) o ON true
LEFT JOIN LATERAL (
    SELECT ex.identity_confirmed, ex.coverage_fraction, ex.close_reason
    FROM public.policy_exposures ex
    WHERE ex.assignment_id=a.assignment_id
    ORDER BY ex.started_at DESC LIMIT 1
) e ON true;

CREATE VIEW verdify_grafana_runtime.blinded_experiment_lifecycle
WITH (security_barrier=true, security_invoker=false) AS
SELECT kind, status, timezone, started_at, updated_at
FROM public.control_experiments;

CREATE VIEW verdify_grafana_runtime.blinded_experiment_events
WITH (security_barrier=true, security_invoker=false) AS
SELECT recorded_at, event_kind, severity, actor FROM public.experiment_events;

ALTER VIEW verdify_grafana_runtime.blinded_experiment_events OWNER TO verdify;
REVOKE ALL ON verdify_grafana_runtime.blinded_experiment_events FROM PUBLIC;
ALTER VIEW verdify_grafana_runtime.blinded_exposure_coverage OWNER TO verdify;
ALTER VIEW verdify_grafana_runtime.blinded_activation_identity OWNER TO verdify;
ALTER VIEW verdify_grafana_runtime.blinded_activation_lineage OWNER TO verdify;
ALTER VIEW verdify_grafana_runtime.blinded_experiment_lifecycle OWNER TO verdify;
REVOKE ALL ON verdify_grafana_runtime.blinded_exposure_coverage,
    verdify_grafana_runtime.blinded_activation_identity,
    verdify_grafana_runtime.blinded_activation_lineage,
    verdify_grafana_runtime.blinded_experiment_lifecycle FROM PUBLIC;
-- Only verdify_grafana_runtime receives SELECT in the complete role migration.


GRANT SELECT ON verdify_grafana_runtime.blinded_exposure_coverage, verdify_grafana_runtime.blinded_activation_identity, verdify_grafana_runtime.blinded_activation_lineage, verdify_grafana_runtime.blinded_experiment_lifecycle, verdify_grafana_runtime.blinded_experiment_events TO verdify_grafana_runtime;

CREATE VIEW verdify_ha_backfill_runtime.climate WITH(security_barrier=true) AS SELECT "ts", "greenhouse_id", "abs_humidity", "co2_ppm", "dew_point", "dli_today", "ec_runoff_center", "enthalpy_delta", "flow_gpm", "house_temp_delta_f", "house_temp_target_f", "house_vpd_delta", "house_vpd_target", "intake_rh", "intake_vpd", "lightning_avg_dist_mi", "lightning_count", "lux", "mister_water_today", "moisture_center", "outdoor_illuminance", "outdoor_lux", "outdoor_rh_pct", "outdoor_temp_f", "ph_runoff_center", "precip_in", "pressure_hpa", "rh_avg", "rh_case", "rh_east", "rh_north", "rh_south", "rh_west", "soil_ec_south_1", "soil_moisture_south_1", "soil_moisture_south_2", "soil_moisture_west", "soil_temp_south_1", "soil_temp_south_2", "soil_temp_west", "solar_irradiance_w_m2", "solar_noon_min", "solar_phase", "solar_sunrise_min", "solar_sunset_min", "temp_avg", "temp_case", "temp_control", "temp_east", "temp_intake", "temp_north", "temp_south", "temp_west", "uv_index", "vpd_avg", "vpd_control", "vpd_delta_center", "vpd_delta_east", "vpd_delta_south", "vpd_delta_west", "vpd_east", "vpd_north", "vpd_south", "vpd_target_center", "vpd_target_east", "vpd_target_south", "vpd_target_west", "vpd_west", "water_total_gal", "wind_direction_deg", "wind_gust_mph", "wind_lull_mph", "wind_speed_mph", "air_density_kg_m3", "feels_like_f", "hydro_battery_pct", "hydro_ec_us_cm", "hydro_orp_mv", "hydro_ph", "hydro_tds_ppm", "hydro_water_temp_f", "precip_intensity_in_h", "vapor_pressure_inhg", "wet_bulb_temp_f", "wind_direction_avg_deg", "wind_speed_avg_mph" FROM public.v_runtime_climate_write;

REVOKE ALL ON verdify_ha_backfill_runtime.climate FROM PUBLIC;

CREATE FUNCTION verdify_ha_backfill_runtime.fn_insert_climate() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $insert$ BEGIN  INSERT INTO public.v_runtime_climate_write("ts", "greenhouse_id", "abs_humidity", "co2_ppm", "dew_point", "dli_today", "ec_runoff_center", "enthalpy_delta", "flow_gpm", "house_temp_delta_f", "house_temp_target_f", "house_vpd_delta", "house_vpd_target", "intake_rh", "intake_vpd", "lightning_avg_dist_mi", "lightning_count", "lux", "mister_water_today", "moisture_center", "outdoor_illuminance", "outdoor_lux", "outdoor_rh_pct", "outdoor_temp_f", "ph_runoff_center", "precip_in", "pressure_hpa", "rh_avg", "rh_case", "rh_east", "rh_north", "rh_south", "rh_west", "soil_ec_south_1", "soil_moisture_south_1", "soil_moisture_south_2", "soil_moisture_west", "soil_temp_south_1", "soil_temp_south_2", "soil_temp_west", "solar_irradiance_w_m2", "solar_noon_min", "solar_phase", "solar_sunrise_min", "solar_sunset_min", "temp_avg", "temp_case", "temp_control", "temp_east", "temp_intake", "temp_north", "temp_south", "temp_west", "uv_index", "vpd_avg", "vpd_control", "vpd_delta_center", "vpd_delta_east", "vpd_delta_south", "vpd_delta_west", "vpd_east", "vpd_north", "vpd_south", "vpd_target_center", "vpd_target_east", "vpd_target_south", "vpd_target_west", "vpd_west", "water_total_gal", "wind_direction_deg", "wind_gust_mph", "wind_lull_mph", "wind_speed_mph", "air_density_kg_m3", "feels_like_f", "hydro_battery_pct", "hydro_ec_us_cm", "hydro_orp_mv", "hydro_ph", "hydro_tds_ppm", "hydro_water_temp_f", "precip_intensity_in_h", "vapor_pressure_inhg", "wet_bulb_temp_f", "wind_direction_avg_deg", "wind_speed_avg_mph") VALUES(NEW."ts", coalesce(NEW.greenhouse_id,'vallery'), NEW."abs_humidity", NEW."co2_ppm", NEW."dew_point", NEW."dli_today", NEW."ec_runoff_center", NEW."enthalpy_delta", NEW."flow_gpm", NEW."house_temp_delta_f", NEW."house_temp_target_f", NEW."house_vpd_delta", NEW."house_vpd_target", NEW."intake_rh", NEW."intake_vpd", NEW."lightning_avg_dist_mi", NEW."lightning_count", NEW."lux", NEW."mister_water_today", NEW."moisture_center", NEW."outdoor_illuminance", NEW."outdoor_lux", NEW."outdoor_rh_pct", NEW."outdoor_temp_f", NEW."ph_runoff_center", NEW."precip_in", NEW."pressure_hpa", NEW."rh_avg", NEW."rh_case", NEW."rh_east", NEW."rh_north", NEW."rh_south", NEW."rh_west", NEW."soil_ec_south_1", NEW."soil_moisture_south_1", NEW."soil_moisture_south_2", NEW."soil_moisture_west", NEW."soil_temp_south_1", NEW."soil_temp_south_2", NEW."soil_temp_west", NEW."solar_irradiance_w_m2", NEW."solar_noon_min", NEW."solar_phase", NEW."solar_sunrise_min", NEW."solar_sunset_min", NEW."temp_avg", NEW."temp_case", NEW."temp_control", NEW."temp_east", NEW."temp_intake", NEW."temp_north", NEW."temp_south", NEW."temp_west", NEW."uv_index", NEW."vpd_avg", NEW."vpd_control", NEW."vpd_delta_center", NEW."vpd_delta_east", NEW."vpd_delta_south", NEW."vpd_delta_west", NEW."vpd_east", NEW."vpd_north", NEW."vpd_south", NEW."vpd_target_center", NEW."vpd_target_east", NEW."vpd_target_south", NEW."vpd_target_west", NEW."vpd_west", NEW."water_total_gal", NEW."wind_direction_deg", NEW."wind_gust_mph", NEW."wind_lull_mph", NEW."wind_speed_mph", NEW."air_density_kg_m3", NEW."feels_like_f", NEW."hydro_battery_pct", NEW."hydro_ec_us_cm", NEW."hydro_orp_mv", NEW."hydro_ph", NEW."hydro_tds_ppm", NEW."hydro_water_temp_f", NEW."precip_intensity_in_h", NEW."vapor_pressure_inhg", NEW."wet_bulb_temp_f", NEW."wind_direction_avg_deg", NEW."wind_speed_avg_mph"); RETURN NEW; END $insert$;

REVOKE ALL ON FUNCTION verdify_ha_backfill_runtime.fn_insert_climate() FROM PUBLIC;

CREATE TRIGGER insert_duty INSTEAD OF INSERT ON verdify_ha_backfill_runtime.climate FOR EACH ROW EXECUTE FUNCTION verdify_ha_backfill_runtime.fn_insert_climate();

GRANT INSERT("ts", "greenhouse_id", "abs_humidity", "co2_ppm", "dew_point", "dli_today", "ec_runoff_center", "enthalpy_delta", "flow_gpm", "house_temp_delta_f", "house_temp_target_f", "house_vpd_delta", "house_vpd_target", "intake_rh", "intake_vpd", "lightning_avg_dist_mi", "lightning_count", "lux", "mister_water_today", "moisture_center", "outdoor_illuminance", "outdoor_lux", "outdoor_rh_pct", "outdoor_temp_f", "ph_runoff_center", "precip_in", "pressure_hpa", "rh_avg", "rh_case", "rh_east", "rh_north", "rh_south", "rh_west", "soil_ec_south_1", "soil_moisture_south_1", "soil_moisture_south_2", "soil_moisture_west", "soil_temp_south_1", "soil_temp_south_2", "soil_temp_west", "solar_irradiance_w_m2", "solar_noon_min", "solar_phase", "solar_sunrise_min", "solar_sunset_min", "temp_avg", "temp_case", "temp_control", "temp_east", "temp_intake", "temp_north", "temp_south", "temp_west", "uv_index", "vpd_avg", "vpd_control", "vpd_delta_center", "vpd_delta_east", "vpd_delta_south", "vpd_delta_west", "vpd_east", "vpd_north", "vpd_south", "vpd_target_center", "vpd_target_east", "vpd_target_south", "vpd_target_west", "vpd_west", "water_total_gal", "wind_direction_deg", "wind_gust_mph", "wind_lull_mph", "wind_speed_mph", "air_density_kg_m3", "feels_like_f", "hydro_battery_pct", "hydro_ec_us_cm", "hydro_orp_mv", "hydro_ph", "hydro_tds_ppm", "hydro_water_temp_f", "precip_intensity_in_h", "vapor_pressure_inhg", "wet_bulb_temp_f", "wind_direction_avg_deg", "wind_speed_avg_mph") ON verdify_ha_backfill_runtime.climate TO verdify_ha_backfill_runtime;

GRANT SELECT ON verdify_ha_backfill_runtime.climate TO verdify_ha_backfill_runtime;

CREATE VIEW verdify_ha_backfill_runtime.diagnostics WITH(security_barrier=true) AS SELECT "ts", "wifi_rssi", "heap_bytes", "heap_min_free_kb", "heap_largest_free_block_kb", "uptime_s", "probe_health", "reset_reason", "firmware_version", "active_probe_count", "relief_cycle_count", "vent_latch_timer_s", "sealed_timer_s", "vpd_watch_timer_s", "mist_backoff_timer_s", "vent_mist_assist_active", "effective_heat_target_f", "effective_cool_stage2_delta_f", "effective_vpd_hysteresis_kpa", "effective_dehum_aggressive_kpa", "controller_time_epoch", "controller_local_hour", "sntp_valid", "sntp_miss_count", "last_sntp_sync_age_s", "band_source", "zone_wet_granted", "greenhouse_id" FROM public.v_runtime_diagnostics_write;

REVOKE ALL ON verdify_ha_backfill_runtime.diagnostics FROM PUBLIC;

CREATE FUNCTION verdify_ha_backfill_runtime.fn_insert_diagnostics() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $insert$ BEGIN  INSERT INTO public.v_runtime_diagnostics_write("ts", "wifi_rssi", "heap_bytes", "heap_min_free_kb", "heap_largest_free_block_kb", "uptime_s", "probe_health", "reset_reason", "firmware_version", "active_probe_count", "relief_cycle_count", "vent_latch_timer_s", "sealed_timer_s", "vpd_watch_timer_s", "mist_backoff_timer_s", "vent_mist_assist_active", "effective_heat_target_f", "effective_cool_stage2_delta_f", "effective_vpd_hysteresis_kpa", "effective_dehum_aggressive_kpa", "controller_time_epoch", "controller_local_hour", "sntp_valid", "sntp_miss_count", "last_sntp_sync_age_s", "band_source", "zone_wet_granted", "greenhouse_id") VALUES(NEW."ts", NEW."wifi_rssi", NEW."heap_bytes", NEW."heap_min_free_kb", NEW."heap_largest_free_block_kb", NEW."uptime_s", NEW."probe_health", NEW."reset_reason", NEW."firmware_version", NEW."active_probe_count", NEW."relief_cycle_count", NEW."vent_latch_timer_s", NEW."sealed_timer_s", NEW."vpd_watch_timer_s", NEW."mist_backoff_timer_s", NEW."vent_mist_assist_active", NEW."effective_heat_target_f", NEW."effective_cool_stage2_delta_f", NEW."effective_vpd_hysteresis_kpa", NEW."effective_dehum_aggressive_kpa", NEW."controller_time_epoch", NEW."controller_local_hour", NEW."sntp_valid", NEW."sntp_miss_count", NEW."last_sntp_sync_age_s", NEW."band_source", NEW."zone_wet_granted", coalesce(NEW.greenhouse_id,'vallery')); RETURN NEW; END $insert$;

REVOKE ALL ON FUNCTION verdify_ha_backfill_runtime.fn_insert_diagnostics() FROM PUBLIC;

CREATE TRIGGER insert_duty INSTEAD OF INSERT ON verdify_ha_backfill_runtime.diagnostics FOR EACH ROW EXECUTE FUNCTION verdify_ha_backfill_runtime.fn_insert_diagnostics();

GRANT INSERT("ts", "wifi_rssi", "heap_bytes", "heap_min_free_kb", "heap_largest_free_block_kb", "uptime_s", "probe_health", "reset_reason", "firmware_version", "active_probe_count", "relief_cycle_count", "vent_latch_timer_s", "sealed_timer_s", "vpd_watch_timer_s", "mist_backoff_timer_s", "vent_mist_assist_active", "effective_heat_target_f", "effective_cool_stage2_delta_f", "effective_vpd_hysteresis_kpa", "effective_dehum_aggressive_kpa", "controller_time_epoch", "controller_local_hour", "sntp_valid", "sntp_miss_count", "last_sntp_sync_age_s", "band_source", "zone_wet_granted", "greenhouse_id") ON verdify_ha_backfill_runtime.diagnostics TO verdify_ha_backfill_runtime;

GRANT SELECT ON verdify_ha_backfill_runtime.diagnostics TO verdify_ha_backfill_runtime;

CREATE VIEW verdify_ha_backfill_runtime.energy WITH(security_barrier=true) AS SELECT "ts", "watts_total", "watts_heat", "watts_fans", "watts_other", "kwh_today", "measurement_revision", "ch0_power_w", "ch1_power_w", "ch0_source_ts", "ch1_source_ts", "ch0_entity_id", "ch1_entity_id", "ch0_quality", "ch1_quality" FROM public.v_runtime_energy_write;

REVOKE ALL ON verdify_ha_backfill_runtime.energy FROM PUBLIC;

CREATE FUNCTION verdify_ha_backfill_runtime.fn_insert_energy() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $insert$ BEGIN  INSERT INTO public.v_runtime_energy_write("ts", "watts_total", "watts_heat", "watts_fans", "watts_other", "kwh_today", "measurement_revision", "ch0_power_w", "ch1_power_w", "ch0_source_ts", "ch1_source_ts", "ch0_entity_id", "ch1_entity_id", "ch0_quality", "ch1_quality") VALUES(NEW."ts", NEW."watts_total", NEW."watts_heat", NEW."watts_fans", NEW."watts_other", NEW."kwh_today", NEW."measurement_revision", NEW."ch0_power_w", NEW."ch1_power_w", NEW."ch0_source_ts", NEW."ch1_source_ts", NEW."ch0_entity_id", NEW."ch1_entity_id", NEW."ch0_quality", NEW."ch1_quality"); RETURN NEW; END $insert$;

REVOKE ALL ON FUNCTION verdify_ha_backfill_runtime.fn_insert_energy() FROM PUBLIC;

CREATE TRIGGER insert_duty INSTEAD OF INSERT ON verdify_ha_backfill_runtime.energy FOR EACH ROW EXECUTE FUNCTION verdify_ha_backfill_runtime.fn_insert_energy();

GRANT INSERT("ts", "watts_total", "watts_heat", "watts_fans", "watts_other", "kwh_today", "measurement_revision", "ch0_power_w", "ch1_power_w", "ch0_source_ts", "ch1_source_ts", "ch0_entity_id", "ch1_entity_id", "ch0_quality", "ch1_quality") ON verdify_ha_backfill_runtime.energy TO verdify_ha_backfill_runtime;

GRANT SELECT ON verdify_ha_backfill_runtime.energy TO verdify_ha_backfill_runtime;

CREATE VIEW verdify_ha_backfill_runtime.equipment_state WITH(security_barrier=true) AS SELECT "equipment", "greenhouse_id", "state", "ts" FROM public.v_runtime_equipment_state_write;

REVOKE ALL ON verdify_ha_backfill_runtime.equipment_state FROM PUBLIC;

CREATE FUNCTION verdify_ha_backfill_runtime.fn_insert_equipment_state() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $insert$ BEGIN  INSERT INTO public.v_runtime_equipment_state_write("equipment", "greenhouse_id", "state", "ts") VALUES(NEW."equipment", coalesce(NEW.greenhouse_id,'vallery'), NEW."state", NEW."ts"); RETURN NEW; END $insert$;

REVOKE ALL ON FUNCTION verdify_ha_backfill_runtime.fn_insert_equipment_state() FROM PUBLIC;

CREATE TRIGGER insert_duty INSTEAD OF INSERT ON verdify_ha_backfill_runtime.equipment_state FOR EACH ROW EXECUTE FUNCTION verdify_ha_backfill_runtime.fn_insert_equipment_state();

GRANT INSERT("equipment", "greenhouse_id", "state", "ts") ON verdify_ha_backfill_runtime.equipment_state TO verdify_ha_backfill_runtime;

GRANT SELECT ON verdify_ha_backfill_runtime.equipment_state TO verdify_ha_backfill_runtime;

CREATE VIEW verdify_ha_backfill_runtime.system_state WITH(security_barrier=true) AS SELECT "ts", "entity", "value", "greenhouse_id" FROM public.v_runtime_system_state_write;

REVOKE ALL ON verdify_ha_backfill_runtime.system_state FROM PUBLIC;

CREATE FUNCTION verdify_ha_backfill_runtime.fn_insert_system_state() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $insert$ BEGIN  INSERT INTO public.v_runtime_system_state_write("ts", "entity", "value", "greenhouse_id") VALUES(NEW."ts", NEW."entity", NEW."value", coalesce(NEW.greenhouse_id,'vallery')); RETURN NEW; END $insert$;

REVOKE ALL ON FUNCTION verdify_ha_backfill_runtime.fn_insert_system_state() FROM PUBLIC;

CREATE TRIGGER insert_duty INSTEAD OF INSERT ON verdify_ha_backfill_runtime.system_state FOR EACH ROW EXECUTE FUNCTION verdify_ha_backfill_runtime.fn_insert_system_state();

GRANT INSERT("ts", "entity", "value", "greenhouse_id") ON verdify_ha_backfill_runtime.system_state TO verdify_ha_backfill_runtime;

GRANT SELECT ON verdify_ha_backfill_runtime.system_state TO verdify_ha_backfill_runtime;

CREATE VIEW verdify_ha_backfill_runtime.setpoint_snapshot WITH(security_barrier=true) AS SELECT "ts", "parameter", "value", "zone", "band_role", "target_value", "greenhouse_id" FROM public.v_runtime_setpoint_snapshot_write;

REVOKE ALL ON verdify_ha_backfill_runtime.setpoint_snapshot FROM PUBLIC;

CREATE FUNCTION verdify_ha_backfill_runtime.fn_insert_setpoint_snapshot() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $insert$ BEGIN  INSERT INTO public.v_runtime_setpoint_snapshot_write("ts", "parameter", "value", "zone", "band_role", "target_value", "greenhouse_id") VALUES(NEW."ts", NEW."parameter", NEW."value", NEW."zone", NEW."band_role", NEW."target_value", coalesce(NEW.greenhouse_id,'vallery')); RETURN NEW; END $insert$;

REVOKE ALL ON FUNCTION verdify_ha_backfill_runtime.fn_insert_setpoint_snapshot() FROM PUBLIC;

CREATE TRIGGER insert_duty INSTEAD OF INSERT ON verdify_ha_backfill_runtime.setpoint_snapshot FOR EACH ROW EXECUTE FUNCTION verdify_ha_backfill_runtime.fn_insert_setpoint_snapshot();

GRANT INSERT("ts", "parameter", "value", "zone", "band_role", "target_value", "greenhouse_id") ON verdify_ha_backfill_runtime.setpoint_snapshot TO verdify_ha_backfill_runtime;

GRANT SELECT ON verdify_ha_backfill_runtime.setpoint_snapshot TO verdify_ha_backfill_runtime;

CREATE VIEW verdify_setpoint_server_runtime.v_runtime_equipment_state_write WITH(security_barrier=true) AS SELECT "ts", "equipment", "state" FROM public.v_runtime_equipment_state_write;

REVOKE ALL ON verdify_setpoint_server_runtime.v_runtime_equipment_state_write FROM PUBLIC;

CREATE FUNCTION verdify_setpoint_server_runtime.fn_insert_v_runtime_equipment_state_write() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $insert$ BEGIN IF NEW.equipment NOT IN ('grow_light_main','grow_light_grow') OR NEW.equipment IS NULL THEN RAISE EXCEPTION 'setpoint-server equipment outside duty' USING ERRCODE='42501'; END IF; INSERT INTO public.v_runtime_equipment_state_write("ts", "equipment", "state") VALUES(NEW."ts", NEW."equipment", NEW."state"); RETURN NEW; END $insert$;

REVOKE ALL ON FUNCTION verdify_setpoint_server_runtime.fn_insert_v_runtime_equipment_state_write() FROM PUBLIC;

CREATE TRIGGER insert_duty INSTEAD OF INSERT ON verdify_setpoint_server_runtime.v_runtime_equipment_state_write FOR EACH ROW EXECUTE FUNCTION verdify_setpoint_server_runtime.fn_insert_v_runtime_equipment_state_write();

GRANT INSERT("ts", "equipment", "state") ON verdify_setpoint_server_runtime.v_runtime_equipment_state_write TO verdify_setpoint_server_runtime;

GRANT SELECT ON verdify_setpoint_server_runtime.v_runtime_equipment_state_write TO verdify_setpoint_server_runtime;

CREATE VIEW verdify_vision_runtime.image_observations WITH(security_barrier=true) AS SELECT "id", "ts", "camera", "zone", "image_path", "model", "raw_response", "crops_observed", "environment_notes", "recommended_actions", "processing_ms", "tokens_used", "confidence" FROM public.image_observations WHERE false;

REVOKE ALL ON verdify_vision_runtime.image_observations FROM PUBLIC;

CREATE FUNCTION verdify_vision_runtime.fn_insert_image_observations() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $insert$ BEGIN  INSERT INTO public.image_observations("ts", "camera", "zone", "image_path", "model", "raw_response", "crops_observed", "environment_notes", "recommended_actions", "processing_ms", "tokens_used", "confidence") VALUES(NEW."ts", NEW."camera", NEW."zone", NEW."image_path", NEW."model", NEW."raw_response", NEW."crops_observed", NEW."environment_notes", NEW."recommended_actions", NEW."processing_ms", NEW."tokens_used", NEW."confidence") RETURNING id INTO NEW.id; RETURN NEW; END $insert$;

REVOKE ALL ON FUNCTION verdify_vision_runtime.fn_insert_image_observations() FROM PUBLIC;

CREATE TRIGGER insert_duty INSTEAD OF INSERT ON verdify_vision_runtime.image_observations FOR EACH ROW EXECUTE FUNCTION verdify_vision_runtime.fn_insert_image_observations();

GRANT INSERT("ts", "camera", "zone", "image_path", "model", "raw_response", "crops_observed", "environment_notes", "recommended_actions", "processing_ms", "tokens_used", "confidence") ON verdify_vision_runtime.image_observations TO verdify_vision_runtime;

GRANT SELECT(id) ON verdify_vision_runtime.image_observations TO verdify_vision_runtime;

CREATE VIEW verdify_vision_runtime.observations WITH(security_barrier=true) AS SELECT "id", "ts", "crop_id", "greenhouse_id", "zone", "position", "zone_id", "position_id", "obs_type", "notes", "source", "health_score", "image_observation_id" FROM public.observations WHERE false;

REVOKE ALL ON verdify_vision_runtime.observations FROM PUBLIC;

CREATE FUNCTION verdify_vision_runtime.fn_insert_observations() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $insert$ BEGIN IF NEW.obs_type IS DISTINCT FROM 'visual_health' OR NEW.source IS DISTINCT FROM 'gemini-vision' THEN RAISE EXCEPTION 'vision observation outside duty' USING ERRCODE='42501'; END IF; INSERT INTO public.observations("ts", "crop_id", "greenhouse_id", "zone", "position", "zone_id", "position_id", "obs_type", "notes", "source", "health_score", "image_observation_id") VALUES(NEW."ts", NEW."crop_id", coalesce(NEW.greenhouse_id,'vallery'), NEW."zone", NEW."position", NEW."zone_id", NEW."position_id", NEW."obs_type", NEW."notes", NEW."source", NEW."health_score", NEW."image_observation_id") RETURNING id INTO NEW.id; RETURN NEW; END $insert$;

REVOKE ALL ON FUNCTION verdify_vision_runtime.fn_insert_observations() FROM PUBLIC;

CREATE TRIGGER insert_duty INSTEAD OF INSERT ON verdify_vision_runtime.observations FOR EACH ROW EXECUTE FUNCTION verdify_vision_runtime.fn_insert_observations();

GRANT INSERT("ts", "crop_id", "greenhouse_id", "zone", "position", "zone_id", "position_id", "obs_type", "notes", "source", "health_score", "image_observation_id") ON verdify_vision_runtime.observations TO verdify_vision_runtime;

GRANT SELECT(id) ON verdify_vision_runtime.observations TO verdify_vision_runtime;

-- Never refresh any predecessor receipt to accept a role-design side effect.
DO $postflight$
BEGIN
 IF EXISTS(SELECT 1 FROM pg_temp.role643_predecessor_seals s
   WHERE s.boundary_sha256 IS DISTINCT FROM CASE WHEN s.login_name='verdify_mcp_runtime_login'
      THEN public.fn_mcp_runtime_boundary_digest()
      ELSE public.fn_runtime_ordinary_boundary_digest(s.login_name) END)
 THEN RAISE EXCEPTION '#643 changed an existing sealed runtime boundary'; END IF;
END $postflight$;
