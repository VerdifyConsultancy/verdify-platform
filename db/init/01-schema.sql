-- Verdify Database Schema (Authoritative)
-- TimescaleDB + PostgreSQL 16
-- 17 tables, 10 views (1 materialized), 3 functions
-- Last updated: 2026-03-22 (Sprint 1 complete)

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ============================================================
-- 1. climate (hypertable)
-- Primary timeseries. ~1 min interval from ESP32, ~1,440 rows/day.
-- 51 columns: 30 original + 2 outdoor + 19 future sensors.
-- NULL = sensor offline or not yet connected.
-- ============================================================
CREATE TABLE IF NOT EXISTS climate (
    ts                  TIMESTAMPTZ NOT NULL,

    -- Temperature (°F)
    temp_avg            FLOAT,
    temp_north          FLOAT,
    temp_south          FLOAT,
    temp_east           FLOAT,
    temp_west           FLOAT,
    temp_case           FLOAT,
    temp_control        FLOAT,
    temp_intake         FLOAT,

    -- Relative Humidity (%)
    rh_avg              FLOAT,
    rh_north            FLOAT,
    rh_south            FLOAT,
    rh_east             FLOAT,
    rh_west             FLOAT,
    rh_case             FLOAT,

    -- VPD (kPa)
    vpd_avg             FLOAT,
    vpd_north           FLOAT,
    vpd_south           FLOAT,
    vpd_east            FLOAT,
    vpd_west            FLOAT,
    vpd_control         FLOAT,

    -- Derived climate
    dew_point           FLOAT,
    abs_humidity        FLOAT,
    enthalpy_delta      FLOAT,

    -- Air quality
    co2_ppm             FLOAT,

    -- Light
    lux                 FLOAT,
    dli_today           FLOAT,

    -- Water
    flow_gpm            FLOAT,
    water_total_gal     FLOAT,

    -- Mister
    mister_water_today  FLOAT,

    -- Outdoor (Open-Meteo API, 5-min sync)
    outdoor_temp_f      FLOAT,
    outdoor_rh_pct      FLOAT,

    -- pH / EC (P1: Atlas Scientific EZO)
    ph_input            FLOAT,
    ec_input            FLOAT,
    ph_runoff_wall      FLOAT,
    ec_runoff_wall      FLOAT,
    ph_runoff_center    FLOAT,
    ec_runoff_center    FLOAT,

    -- Substrate moisture (P1: STEMMA capacitive)
    moisture_north      FLOAT,
    moisture_south      FLOAT,
    moisture_center     FLOAT,

    -- PAR / DLI (P1: Apogee SQ-520)
    ppfd                FLOAT,
    dli_par_today       FLOAT,

    -- Barometric pressure (P2: BME280)
    pressure_hpa        FLOAT,

    -- Leaf temperature (P3: MLX90614 IR)
    leaf_temp_north     FLOAT,
    leaf_temp_south     FLOAT,

    -- Leaf wetness (P3: capacitive)
    leaf_wetness_north  FLOAT,
    leaf_wetness_south  FLOAT,

    -- Wind (P3: anemometer or Open-Meteo)
    wind_speed_mph      FLOAT,
    wind_direction_deg  FLOAT,

    -- Outdoor PAR (P3)
    outdoor_ppfd        FLOAT
);

SELECT create_hypertable('climate', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_climate_ts ON climate (ts DESC);


-- ============================================================
-- 2. equipment_state (hypertable)
-- Relay on/off events, written on state change.
-- ============================================================
CREATE TABLE IF NOT EXISTS equipment_state (
    ts          TIMESTAMPTZ NOT NULL,
    equipment   TEXT NOT NULL,
    state       BOOLEAN NOT NULL
);

SELECT create_hypertable('equipment_state', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_equipment_state_ts ON equipment_state (ts DESC);
CREATE INDEX IF NOT EXISTS idx_equipment_state_equip ON equipment_state (equipment, ts DESC);


-- ============================================================
-- 3. system_state (hypertable)
-- State machine transitions, written on change.
-- ============================================================
CREATE TABLE IF NOT EXISTS system_state (
    ts      TIMESTAMPTZ NOT NULL,
    entity  TEXT NOT NULL,
    value   TEXT NOT NULL
);

SELECT create_hypertable('system_state', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_system_state_ts ON system_state (ts DESC);
CREATE INDEX IF NOT EXISTS idx_system_state_entity ON system_state (entity, ts DESC);


-- ============================================================
-- 4. daily_summary
-- Midnight snapshot. One row per day at 00:05 UTC.
-- ============================================================
CREATE TABLE IF NOT EXISTS daily_summary (
    date                    DATE PRIMARY KEY,
    cycles_fan1             INT,
    cycles_fan2             INT,
    cycles_heat1            INT,
    cycles_heat2            INT,
    cycles_fog              INT,
    cycles_vent             INT,
    cycles_dehum            INT,
    cycles_safety_dehum     INT,
    runtime_fan1_min        FLOAT,
    runtime_fan2_min        FLOAT,
    runtime_heat1_min       FLOAT,
    runtime_heat2_min       FLOAT,
    runtime_fog_min         FLOAT,
    runtime_vent_min        FLOAT,
    runtime_mister_south_h  FLOAT,
    runtime_mister_west_h   FLOAT,
    runtime_mister_center_h FLOAT,
    water_used_gal          FLOAT,
    mister_water_gal        FLOAT,
    dli_final               FLOAT,
    captured_at             TIMESTAMPTZ DEFAULT NOW(),
    kwh_total               FLOAT,
    kwh_heat                FLOAT,
    kwh_fans                FLOAT,
    kwh_other               FLOAT,
    peak_kw                 FLOAT,
    gas_used_therms         FLOAT,
    runtime_grow_light_min  FLOAT,
    cycles_grow_light       INT
);


-- ============================================================
-- 5. setpoint_changes (hypertable)
-- All 51 tunable parameter changes.
-- ============================================================
CREATE TABLE IF NOT EXISTS setpoint_changes (
    ts          TIMESTAMPTZ NOT NULL,
    parameter   TEXT NOT NULL,
    value       FLOAT NOT NULL,
    source      TEXT DEFAULT 'esp32'
);

SELECT create_hypertable('setpoint_changes', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_setpoints_ts ON setpoint_changes (ts DESC);
CREATE INDEX IF NOT EXISTS idx_setpoints_param ON setpoint_changes (parameter, ts DESC);


-- ============================================================
-- 6. diagnostics (hypertable)
-- ESP32 health metrics, ~60s interval.
-- ============================================================
CREATE TABLE IF NOT EXISTS diagnostics (
    ts              TIMESTAMPTZ NOT NULL,
    wifi_rssi       FLOAT,
    heap_bytes      FLOAT,
    uptime_s        FLOAT,
    probe_health    TEXT,
    reset_reason    TEXT
);

SELECT create_hypertable('diagnostics', 'ts', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_diagnostics_ts ON diagnostics (ts DESC);


-- ============================================================
-- 7. crops (P0 — keystone table)
-- Every crop metric chains back here.
-- ============================================================
CREATE TABLE IF NOT EXISTS crops (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    variety         TEXT,
    position        TEXT NOT NULL,
    zone            TEXT NOT NULL,
    planted_date    DATE NOT NULL,
    expected_harvest DATE,
    stage           TEXT DEFAULT 'seed',
    count           INT,
    seed_lot_id     TEXT,
    supplier        TEXT,
    base_temp_f     FLOAT DEFAULT 50.0,
    target_dli      FLOAT,
    target_vpd_low  FLOAT,
    target_vpd_high FLOAT,
    notes           TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_crops_active ON crops(is_active, zone);


-- ============================================================
-- 8. crop_events (P0)
-- Stage changes, transplants, pruning, removals.
-- ============================================================
CREATE TABLE IF NOT EXISTS crop_events (
    id          SERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ DEFAULT now(),
    crop_id     INT REFERENCES crops(id),
    event_type  TEXT NOT NULL,
    old_stage   TEXT,
    new_stage   TEXT,
    count       INT,
    operator    TEXT,
    source      TEXT DEFAULT 'manual',
    notes       TEXT
);

CREATE INDEX IF NOT EXISTS idx_crop_events_crop ON crop_events(crop_id, ts);


-- ============================================================
-- 9. observations (P0)
-- Pest scouting, disease, growth notes.
-- ============================================================
CREATE TABLE IF NOT EXISTS observations (
    id           SERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ DEFAULT now(),
    obs_type     TEXT NOT NULL,
    zone         TEXT,
    position     TEXT,
    severity     INT CHECK(severity BETWEEN 1 AND 5),
    species      TEXT,
    count        INT,
    affected_pct FLOAT,
    crop_id      INT REFERENCES crops(id),
    photo_path   TEXT,
    observer     TEXT,
    source       TEXT DEFAULT 'manual',
    notes        TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_ts ON observations(ts DESC);
CREATE INDEX IF NOT EXISTS idx_obs_type ON observations(obs_type, zone, ts);


-- ============================================================
-- 10. treatments (P0)
-- Spray/biological applications with PHI/REI.
-- ============================================================
CREATE TABLE IF NOT EXISTS treatments (
    id                SERIAL PRIMARY KEY,
    ts                TIMESTAMPTZ DEFAULT now(),
    product           TEXT NOT NULL,
    active_ingredient TEXT,
    concentration     FLOAT,
    rate              FLOAT,
    rate_unit         TEXT,
    method            TEXT,
    zone              TEXT,
    crop_id           INT REFERENCES crops(id),
    target_pest       TEXT,
    phi_days          INT,
    rei_hours         INT,
    applicator        TEXT,
    observation_id    INT REFERENCES observations(id),
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS idx_treatments_ts ON treatments(ts DESC);
CREATE INDEX IF NOT EXISTS idx_treatments_phi ON treatments(crop_id, ts)
    WHERE phi_days IS NOT NULL;


-- ============================================================
-- 11. harvests (P0)
-- Yield records. Denominator for all efficiency metrics.
-- ============================================================
CREATE TABLE IF NOT EXISTS harvests (
    id            SERIAL PRIMARY KEY,
    ts            TIMESTAMPTZ DEFAULT now(),
    crop_id       INT REFERENCES crops(id),
    weight_kg     FLOAT,
    unit_count    INT,
    quality_grade TEXT,
    zone          TEXT,
    destination   TEXT,
    unit_price    FLOAT,
    revenue       FLOAT,
    operator      TEXT,
    notes         TEXT
);


-- ============================================================
-- 12. energy (hypertable, P1)
-- CT clamp / smart meter high-frequency power monitoring.
-- ============================================================
CREATE TABLE IF NOT EXISTS energy (
    ts            TIMESTAMPTZ NOT NULL,
    watts_total   FLOAT,
    watts_heat    FLOAT,
    watts_fans    FLOAT,
    watts_other   FLOAT,
    kwh_today     FLOAT
);

SELECT create_hypertable('energy', 'ts', if_not_exists => TRUE);


-- ============================================================
-- 13. weather_forecast (hypertable, P1)
-- 72-hour hourly forecast from Open-Meteo.
-- ============================================================
CREATE TABLE IF NOT EXISTS weather_forecast (
    ts              TIMESTAMPTZ NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL,
    temp_f          FLOAT,
    rh_pct          FLOAT,
    wind_speed_mph  FLOAT,
    wind_dir_deg    FLOAT,
    cloud_cover_pct FLOAT,
    precip_prob_pct FLOAT,
    solar_w_m2      FLOAT
);

SELECT create_hypertable('weather_forecast', 'ts', if_not_exists => TRUE);


-- ============================================================
-- 14. equipment_assets (P2)
-- Hardware inventory with rated wattage.
-- ============================================================
CREATE TABLE IF NOT EXISTS equipment_assets (
    id           SERIAL PRIMARY KEY,
    equipment    TEXT UNIQUE NOT NULL,
    description  TEXT,
    model        TEXT,
    serial_no    TEXT,
    install_date DATE,
    warranty_exp DATE,
    wattage      FLOAT,
    btu_rating   FLOAT,
    notes        TEXT
);


-- ============================================================
-- 15. maintenance_log (P2)
-- Preventive maintenance tracking.
-- ============================================================
CREATE TABLE IF NOT EXISTS maintenance_log (
    id           SERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ DEFAULT now(),
    equipment    TEXT REFERENCES equipment_assets(equipment),
    service_type TEXT,
    description  TEXT,
    cost         FLOAT,
    technician   TEXT,
    next_due     DATE,
    notes        TEXT
);


-- ============================================================
-- 16. nutrient_recipes (P2)
-- Fertigation recipe vs. actual comparison.
-- ============================================================
CREATE TABLE IF NOT EXISTS nutrient_recipes (
    id               SERIAL PRIMARY KEY,
    name             TEXT NOT NULL,
    crop_id          INT REFERENCES crops(id),
    stage            TEXT,
    target_ec        FLOAT,
    target_ph_low    FLOAT,
    target_ph_high   FLOAT,
    n_ppm            FLOAT,
    p_ppm            FLOAT,
    k_ppm            FLOAT,
    ca_ppm           FLOAT,
    mg_ppm           FLOAT,
    fe_ppm           FLOAT,
    stock_a_ml_per_l FLOAT,
    stock_b_ml_per_l FLOAT,
    notes            TEXT,
    is_active        BOOLEAN DEFAULT TRUE,
    created_at       TIMESTAMPTZ DEFAULT now()
);


-- ============================================================
-- 17. lab_results (P2)
-- Manual entry from lab reports.
-- ============================================================
CREATE TABLE IF NOT EXISTS lab_results (
    id          SERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ DEFAULT now(),
    sample_type TEXT NOT NULL,
    zone        TEXT,
    crop_id     INT REFERENCES crops(id),
    lab_name    TEXT,
    sampled_at  DATE,
    ph          FLOAT,
    ec_ms_cm    FLOAT,
    n_pct       FLOAT,  p_pct  FLOAT,  k_pct  FLOAT,
    ca_pct      FLOAT,  mg_pct FLOAT,  fe_ppm FLOAT,
    mn_ppm      FLOAT,  zn_ppm FLOAT,  b_ppm  FLOAT,
    cu_ppm      FLOAT,  na_ppm FLOAT,  cl_ppm FLOAT,
    notes       TEXT
);


-- ============================================================
-- Views
-- ============================================================

CREATE OR REPLACE VIEW v_climate_latest AS
SELECT * FROM climate ORDER BY ts DESC LIMIT 1;

CREATE OR REPLACE VIEW v_water_daily AS
SELECT date_trunc('day', ts) AS day,
    max(water_total_gal) - min(water_total_gal) AS used_gal
FROM climate WHERE water_total_gal IS NOT NULL
GROUP BY 1 ORDER BY 1 DESC;

CREATE OR REPLACE VIEW v_zone_temp_delta AS
SELECT ts, (temp_north - temp_south) AS zone_temp_delta
FROM climate WHERE temp_north IS NOT NULL AND temp_south IS NOT NULL;

CREATE OR REPLACE VIEW v_stress_hours_today AS
WITH latest_setpoints AS (
    SELECT DISTINCT ON (parameter) parameter, value::float AS value
    FROM setpoint_changes ORDER BY parameter, ts DESC
)
SELECT
    date_trunc('day', ts) AS date,
    ROUND(SUM(CASE WHEN temp_avg < (SELECT value FROM latest_setpoints WHERE parameter = 'temp_low') THEN 2.0/60.0 ELSE 0 END)::numeric, 2) AS cold_stress_hours,
    ROUND(SUM(CASE WHEN temp_avg > (SELECT value FROM latest_setpoints WHERE parameter = 'temp_high') THEN 2.0/60.0 ELSE 0 END)::numeric, 2) AS heat_stress_hours,
    ROUND(SUM(CASE WHEN vpd_avg > (SELECT value FROM latest_setpoints WHERE parameter = 'vpd_high') THEN 2.0/60.0 ELSE 0 END)::numeric, 2) AS vpd_stress_hours,
    ROUND(SUM(CASE WHEN vpd_avg < (SELECT value FROM latest_setpoints WHERE parameter = 'vpd_low') THEN 2.0/60.0 ELSE 0 END)::numeric, 2) AS vpd_low_hours
FROM climate WHERE temp_avg IS NOT NULL AND vpd_avg IS NOT NULL
GROUP BY 1 ORDER BY 1;

CREATE OR REPLACE VIEW v_equipment_runtime_today AS
SELECT equipment,
    sum(CASE WHEN state = TRUE THEN 1 ELSE 0 END) AS on_events,
    sum(CASE WHEN state = FALSE THEN 1 ELSE 0 END) AS off_events
FROM equipment_state WHERE ts >= date_trunc('day', now())
GROUP BY equipment;

CREATE OR REPLACE VIEW v_disease_risk AS
WITH recent AS (
    SELECT ts, rh_avg, temp_avg, vpd_avg FROM climate
    WHERE ts >= now() - INTERVAL '24 hours' AND rh_avg IS NOT NULL AND temp_avg IS NOT NULL
),
flags AS (
    SELECT ts,
        CASE WHEN rh_avg > 85 AND temp_avg BETWEEN 60 AND 80 THEN 1 ELSE 0 END AS botrytis_flag,
        CASE WHEN vpd_avg < 0.4 THEN 1 ELSE 0 END AS condensation_flag
    FROM recent
)
SELECT date_trunc('hour', ts) AS hour,
    ROUND(AVG(botrytis_flag)::numeric * 100, 1) AS botrytis_risk_pct,
    ROUND(AVG(condensation_flag)::numeric * 100, 1) AS condensation_risk_pct,
    ROUND((SUM(botrytis_flag) * 2.0 / 60.0)::numeric, 2) AS botrytis_consecutive_hours,
    ROUND((SUM(condensation_flag) * 2.0 / 60.0)::numeric, 2) AS condensation_consecutive_hours
FROM flags GROUP BY 1 ORDER BY 1;

CREATE OR REPLACE VIEW v_dif AS
SELECT date_trunc('day', ts AT TIME ZONE 'America/Denver') AS date,
    ROUND(AVG(CASE WHEN EXTRACT(HOUR FROM ts AT TIME ZONE 'America/Denver') BETWEEN 7 AND 18 THEN temp_avg END)::numeric, 2) AS day_avg_temp,
    ROUND(AVG(CASE WHEN EXTRACT(HOUR FROM ts AT TIME ZONE 'America/Denver') NOT BETWEEN 7 AND 18 THEN temp_avg END)::numeric, 2) AS night_avg_temp,
    ROUND((AVG(CASE WHEN EXTRACT(HOUR FROM ts AT TIME ZONE 'America/Denver') BETWEEN 7 AND 18 THEN temp_avg END) -
      AVG(CASE WHEN EXTRACT(HOUR FROM ts AT TIME ZONE 'America/Denver') NOT BETWEEN 7 AND 18 THEN temp_avg END))::numeric, 2) AS dif
FROM climate WHERE temp_avg IS NOT NULL
GROUP BY 1 ORDER BY 1;

CREATE OR REPLACE VIEW v_water_efficiency AS
SELECT ds.date, ds.water_used_gal, ds.dli_final,
    CASE WHEN ds.dli_final > 0 THEN ROUND((ds.water_used_gal / ds.dli_final)::numeric, 3) ELSE NULL END AS gal_per_mol_dli
FROM daily_summary ds WHERE ds.water_used_gal IS NOT NULL ORDER BY ds.date;

CREATE OR REPLACE VIEW v_gdd AS
WITH daily_temps AS (
    SELECT date_trunc('day', ts) AS date, AVG(temp_avg) AS avg_temp_f
    FROM climate WHERE temp_avg IS NOT NULL GROUP BY 1
)
SELECT c.id AS crop_id, c.name, c.position, dt.date,
    GREATEST(0, dt.avg_temp_f - c.base_temp_f) AS gdd_day,
    SUM(GREATEST(0, dt.avg_temp_f - c.base_temp_f))
        OVER (PARTITION BY c.id ORDER BY dt.date ROWS UNBOUNDED PRECEDING) AS gdd_cumulative
FROM crops c JOIN daily_temps dt ON dt.date >= c.planted_date::timestamptz
WHERE c.is_active = TRUE ORDER BY c.id, dt.date;

-- Materialized view (refreshed every 5 min via TimescaleDB job 1000)
CREATE MATERIALIZED VIEW IF NOT EXISTS v_relay_stuck AS
WITH latest_on AS (
    SELECT equipment, MAX(ts) AS last_on_ts FROM equipment_state WHERE state = TRUE GROUP BY equipment
),
latest_off AS (
    SELECT equipment, MAX(ts) AS last_off_ts FROM equipment_state WHERE state = FALSE GROUP BY equipment
),
stuck_check AS (
    SELECT lo.equipment, lo.last_on_ts, lf.last_off_ts,
        EXTRACT(EPOCH FROM (now() - lo.last_on_ts)) / 3600.0 AS hours_on,
        CASE lo.equipment
            WHEN 'heat1' THEN 3 WHEN 'heat2' THEN 3
            WHEN 'fan1' THEN 4 WHEN 'fan2' THEN 4
            WHEN 'fog' THEN 2 WHEN 'vent' THEN 6 ELSE 8
        END AS threshold_hours
    FROM latest_on lo LEFT JOIN latest_off lf ON lo.equipment = lf.equipment
    WHERE lo.last_on_ts > COALESCE(lf.last_off_ts, '1970-01-01'::timestamptz)
)
SELECT equipment, last_on_ts, hours_on, threshold_hours, (hours_on > threshold_hours) AS is_stuck
FROM stuck_check;


-- ============================================================
-- Functions
-- ============================================================

CREATE OR REPLACE FUNCTION fn_equipment_health()
RETURNS INT AS $$
DECLARE
    score INT := 100;
    rssi FLOAT;
    heap FLOAT;
    stuck_count INT;
    probe TEXT;
BEGIN
    SELECT wifi_rssi, heap_bytes, probe_health INTO rssi, heap, probe
    FROM diagnostics ORDER BY ts DESC LIMIT 1;
    IF rssi IS NOT NULL THEN
        IF rssi < -85 THEN score := score - 25;
        ELSIF rssi < -75 THEN score := score - 10; END IF;
    END IF;
    IF heap IS NOT NULL THEN
        IF heap < 15000 THEN score := score - 25;
        ELSIF heap < 30000 THEN score := score - 10; END IF;
    END IF;
    BEGIN
        SELECT COUNT(*) INTO stuck_count FROM v_relay_stuck WHERE is_stuck = TRUE;
        score := score - (stuck_count * 20);
    EXCEPTION WHEN undefined_table THEN NULL;
    END;
    IF probe IS NOT NULL AND probe != 'OK' THEN score := score - 15; END IF;
    RETURN GREATEST(0, score);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION fn_stress_summary(target_date DATE DEFAULT CURRENT_DATE)
RETURNS TEXT AS $$
DECLARE cold NUMERIC; heat NUMERIC; vpd_hi NUMERIC; vpd_lo NUMERIC;
BEGIN
    SELECT cold_stress_hours, heat_stress_hours, vpd_stress_hours, vpd_low_hours
    INTO cold, heat, vpd_hi, vpd_lo FROM v_stress_hours_today WHERE date = target_date::timestamptz;
    IF cold IS NULL THEN RETURN 'No data for ' || target_date::text; END IF;
    RETURN cold || 'h cold | ' || heat || 'h heat | ' || vpd_hi || 'h VPD high | ' || vpd_lo || 'h VPD low';
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION refresh_relay_stuck()
RETURNS VOID AS $$
BEGIN REFRESH MATERIALIZED VIEW v_relay_stuck; END;
$$ LANGUAGE plpgsql;

-- Schedule refresh_relay_stuck every 5 minutes (TimescaleDB job scheduler)
-- Run manually if rebuilding: SELECT add_job('refresh_relay_stuck', '5 minutes');
