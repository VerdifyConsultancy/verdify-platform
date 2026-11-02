-- 283 (VD-38, #1153), dashboard migration M3: the irrigation program's tables, seeds and grants.
-- irrigation_line holds the 11 lines, seeded; irrigation_slot the schedules, whose ids come from
-- a sequence that never cycles, so an id is never reused; irrigation_command the run, stop,
-- clear-fault and override requests; irrigation_push each compiled program sent; and
-- irrigation_ledger the engine's outcomes. v_irrigation_line_state adds each line's newest relay
-- edge and the raw irr_state and irr_events text. 281's minutes view and reader now take cause
-- and slot from the ledger, and an L01 run that starts within 30 s after a sent bridge command
-- reads run_now. The dashboard and MCP logins write rows; only the ingestor turns them into device
-- writes. No trigger, no NOTIFY payload, no grant on setpoint_changes; inert until a service reads
-- the tables. Its predecessor is exact282, which kept 281's qualified seals. Its grants reach 282's
-- dashboard duty on tables the ingestor reads, so both ordinary digests hash that duty's
-- production OID: the rolled-back native qualification measures this file once 282 is applied.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
        AND seq=282 AND filename='db/migrations/282-dashboard-runtime-login.sql'
        AND sha256='b58f0df3b66f79dc968aee502d41b4860e269285c5369dc0a06273c44908377a'
        AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=283)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'aa226e5fb29a9aa0548c597ef718236d0d83737cb23c322920977a9655958dc7' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '2449cf2fc05464a4deedb5f78de6da6d3208c870dd9f841133d3c8fe5f0513db' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '9cb30130e47e95af13af14f2fc08c89e2422e89e14bd3b448e4448849ab194b3'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('aa226e5fb29a9aa0548c597ef718236d0d83737cb23c322920977a9655958dc7','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('2449cf2fc05464a4deedb5f78de6da6d3208c870dd9f841133d3c8fe5f0513db','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('9cb30130e47e95af13af14f2fc08c89e2422e89e14bd3b448e4448849ab194b3','hex'))
        OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='verdify_dashboard_runtime' AND NOT rolcanlogin)
        OR to_regclass('public.irrigation_line') IS NOT NULL OR to_regclass('public.irrigation_slot') IS NOT NULL
        OR to_regclass('public.irrigation_command') IS NOT NULL OR to_regclass('public.irrigation_push') IS NOT NULL
        OR to_regclass('public.irrigation_ledger') IS NOT NULL OR to_regclass('public.v_irrigation_line_state') IS NOT NULL
    THEN RAISE EXCEPTION '283 refuses an unqualified predecessor'; END IF;
END $predecessor$;

-- Every table carries greenhouse_id and the R4 attribution: source (dashboard, iris,
-- agent:<name> or system), the actor's name and a reason of at most 500 characters.
-- Per-kind schedule bounds live in verdify_schemas.irrigation; the tables check only the
-- program record's rules (irrigation 4.3 and 5) and each field's range.
CREATE TABLE public.irrigation_line (
    line_id         text PRIMARY KEY CHECK (line_id IN ('L01', 'L02', 'L03', 'L04', 'L05', 'L06',
                                                        'L07', 'L08', 'L09', 'L10', 'L11')),
    greenhouse_id   text NOT NULL DEFAULT 'vallery',
    name            text NOT NULL,
    zone            text NOT NULL CHECK (zone IN ('wall', 'supply', 'center', 'south', 'west', 'house')),
    kind            text NOT NULL CHECK (kind IN ('drip', 'mister', 'fert_drip', 'fert_mister', 'master', 'fog')),
    relay           text NOT NULL UNIQUE,
    equipment       text NOT NULL UNIQUE,
    water_system    text,
    device_idx      smallint NOT NULL UNIQUE CHECK (device_idx BETWEEN 0 AND 10),
    clean_partner   text REFERENCES public.irrigation_line (line_id),
    reserved        boolean NOT NULL DEFAULT false,
    enabled         boolean NOT NULL DEFAULT false,
    vpd_member      boolean,
    schedule_member boolean,
    daily_cap_min   integer NOT NULL DEFAULT 0 CHECK (daily_cap_min BETWEEN 0 AND 480),
    vpd_reserve_min integer CHECK (vpd_reserve_min >= 0),
    paused_until    timestamptz,
    manual          boolean NOT NULL DEFAULT false,
    checked_by      text,
    checked_at      timestamptz,
    check_note      text CHECK (length(check_note) <= 500),
    source          text NOT NULL,
    updated_by      text NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    reason          text CHECK (length(reason) <= 500),
    CONSTRAINT irrigation_line_vpd_member_misters
        CHECK ((vpd_member IS NULL) = (line_id NOT IN ('L04', 'L07', 'L09'))),
    CONSTRAINT irrigation_line_schedule_member_misters
        CHECK ((schedule_member IS NULL) = (line_id NOT IN ('L04', 'L07', 'L09'))),
    CONSTRAINT irrigation_line_reserve_within_cap CHECK (vpd_reserve_min <= daily_cap_min),
    CONSTRAINT irrigation_line_check_who_and_when CHECK ((checked_by IS NULL) = (checked_at IS NULL)),
    CONSTRAINT irrigation_line_check_note_needs_check CHECK (check_note IS NULL OR checked_at IS NOT NULL)
);
COMMENT ON TABLE public.irrigation_line IS
    'The 11 irrigation lines (VD-38, irrigation 3 and 4.2). Topology columns are seeded; people and agents edit the enable, flags, caps, pause and manual; only a person records the check (checked_by, checked_at, check_note).';

CREATE TABLE public.irrigation_slot (
    slot_id       integer GENERATED ALWAYS AS IDENTITY (SEQUENCE NAME public.irrigation_slot_slot_id_seq
                      START WITH 4 MINVALUE 1 MAXVALUE 65535 NO CYCLE) PRIMARY KEY,
    greenhouse_id text NOT NULL DEFAULT 'vallery',
    line_id       text NOT NULL REFERENCES public.irrigation_line (line_id),
    enabled       boolean NOT NULL DEFAULT true,
    days          smallint NOT NULL CHECK (days BETWEEN 1 AND 127),
    start_kind    text NOT NULL CHECK (start_kind IN ('clock', 'sunrise', 'solar_noon', 'sunset')),
    start_min     smallint NOT NULL,
    on_s          integer NOT NULL CHECK (on_s BETWEEN 10 AND 7200),
    count         integer NOT NULL DEFAULT 1 CHECK (count BETWEEN 1 AND 60),
    gap_s         integer NOT NULL DEFAULT 0 CHECK (gap_s BETWEEN 0 AND 600),
    prewet_s      integer NOT NULL DEFAULT 0 CHECK (prewet_s BETWEEN 0 AND 1800),
    flush_s       integer NOT NULL DEFAULT 0 CHECK (flush_s BETWEEN 0 AND 1800),
    degraded_only boolean NOT NULL DEFAULT false,
    label         text CHECK (char_length(label) <= 60),
    manual        boolean NOT NULL DEFAULT false,
    source        text NOT NULL,
    updated_by    text NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    reason        text CHECK (length(reason) <= 500),
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT irrigation_slot_slot_id_range CHECK (slot_id BETWEEN 1 AND 65535),
    CONSTRAINT irrigation_slot_schedulable_line CHECK (line_id NOT IN ('L03', 'L11')),
    CONSTRAINT irrigation_slot_degraded_only_misters CHECK (NOT degraded_only OR line_id IN ('L04', 'L07', 'L09')),
    CONSTRAINT irrigation_slot_start_range CHECK (CASE WHEN start_kind = 'clock' THEN start_min BETWEEN 0 AND 1439
                                                      ELSE start_min BETWEEN -240 AND 240 END)
);
COMMENT ON TABLE public.irrigation_slot IS
    'Irrigation schedules (VD-38, irrigation 4.3); the device calls each one a slot. days is a weekday mask (bit 0 Monday); start_min is a local clock minute or the offset from the solar time. Ids come from irrigation_slot_slot_id_seq, which never cycles, so a deleted schedule''s id is never reused.';

CREATE TABLE public.irrigation_command (
    command_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    greenhouse_id text NOT NULL DEFAULT 'vallery',
    kind          text NOT NULL CHECK (kind IN ('run', 'stop', 'clear_fault', 'override')),
    line_id       text REFERENCES public.irrigation_line (line_id),
    args          jsonb NOT NULL DEFAULT '{}',
    status        text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    fail_reason   text,
    source        text NOT NULL,
    actor         text NOT NULL,
    reason        text CHECK (length(reason) <= 500),
    created_at    timestamptz NOT NULL DEFAULT now(),
    sent_at       timestamptz
);
CREATE INDEX irrigation_command_pending ON public.irrigation_command (created_at) WHERE status = 'pending';
-- The minutes view's run-now lookup: sent run commands by line and send time.
CREATE INDEX irrigation_command_sent_runs ON public.irrigation_command (greenhouse_id, line_id, sent_at)
    WHERE kind = 'run' AND status = 'sent';
COMMENT ON TABLE public.irrigation_command IS
    'Run, stop, clear-fault and override requests (VD-38, irrigation 6.1). line_id NULL means every line. The ingestor marks a row sent just before it calls the device, or failed. Idempotency lives on the write''s activity_log row.';

CREATE TABLE public.irrigation_push (
    revision        bigint GENERATED ALWAYS AS IDENTITY (SEQUENCE NAME public.irrigation_push_revision_seq) PRIMARY KEY,
    greenhouse_id   text NOT NULL DEFAULT 'vallery',
    sha256          text NOT NULL,
    bytes           bytea NOT NULL,
    site_desired_at timestamptz,
    pushed_at       timestamptz,
    confirmed_at    timestamptz,
    reject_code     text,
    CONSTRAINT irrigation_push_record CHECK (octet_length(bytes) = 584
                                             AND sha256 = encode(pg_catalog.sha256(bytes), 'hex'))
);
COMMENT ON TABLE public.irrigation_push IS
    'Each compiled program record sent (VD-38, irrigation 5 and 6.1): the 584 B VIP1 record and its SHA-256. The ingestor adds a row only when the hash changes; confirmed_at is set once irr_program_id reports it.';

CREATE TABLE public.irrigation_ledger (
    id            bigserial PRIMARY KEY,
    greenhouse_id text NOT NULL DEFAULT 'vallery',
    line_id       text NOT NULL REFERENCES public.irrigation_line (line_id),
    slot_id       integer CHECK (slot_id BETWEEN 1 AND 65535),
    local_day     date NOT NULL,
    cause         text NOT NULL CHECK (cause IN ('vpd', 'schedule', 'run_now', 'feed', 'prewet', 'flush',
                                                 'fallback', 'unknown')),
    started_at    timestamptz,
    ended_at      timestamptz,
    open_s        integer NOT NULL DEFAULT 0 CHECK (open_s >= 0),
    pulses        integer CHECK (pulses >= 0),
    outcome       text CHECK (outcome IN ('ok', 'short', 'stopped', 'preempted', 'skipped', 'missed', 'cap',
                                          'unknown')),
    reason        text CHECK (length(reason) <= 500),
    event_seq     integer CHECK (event_seq BETWEEN 0 AND 65535),
    command_id    uuid REFERENCES public.irrigation_command (command_id),
    actor         text,
    recorded_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX irrigation_ledger_occurrence ON public.irrigation_ledger (greenhouse_id, slot_id, local_day, cause)
    WHERE slot_id IS NOT NULL;
-- The minutes view's per-run lookup: a line's ledger rows by start.
CREATE INDEX irrigation_ledger_line_started ON public.irrigation_ledger (greenhouse_id, line_id, started_at);
COMMENT ON TABLE public.irrigation_ledger IS
    'The engine''s outcomes (VD-38, irrigation 6.2), written by the ingestor: one row per occurrence, fertilizer stage or run-now. line_id is the line whose relay opened; slot_id has no foreign key, so history outlives a deleted schedule, and is NULL for run-now; outcome is NULL until known; event_seq is the irr_events sequence number.';

-- Seeds (source system, actor migration, manual false, no check on any line): irrigation 3 with
-- the D-IRP-06 caps; L01 and L04 enabled (D-IRP-07, Round 2 item 4); L05 and L06 reserved (Round
-- 2b E); L11's enable gates nothing, fog stays with the climate controller.
INSERT INTO public.irrigation_line
    (line_id, name, zone, kind, relay, equipment, water_system, device_idx, clean_partner, reserved,
     enabled, vpd_member, schedule_member, daily_cap_min, vpd_reserve_min, source, updated_by, reason)
VALUES
    ('L01', 'Drip • Wall', 'wall', 'drip', 'wall_drips', 'drip_wall', 'wall_drip_clean',
     0, NULL, false, true, NULL, NULL, 60, NULL, 'system', 'migration', 'Seeded by migration 283 (irrigation 3)'),
    ('L02', 'Drip • Wall (Fert.)', 'wall', 'fert_drip', 'wall_drips_fertilized', 'drip_wall_fert', 'wall_drip_fert',
     1, 'L01', false, false, NULL, NULL, 0, NULL, 'system', 'migration', 'Seeded by migration 283 (irrigation 3)'),
    ('L03', 'Valve • Fert. Master', 'supply', 'master', 'fertilizer_master_valve', 'fert_master_valve', NULL,
     2, NULL, false, false, NULL, NULL, 0, NULL, 'system', 'migration', 'Seeded by migration 283 (irrigation 3)'),
    ('L04', 'Mister • Center', 'center', 'mister', 'center_mister', 'mister_center', 'center_mister_clean',
     3, NULL, false, true, true, false, 120, 60, 'system', 'migration', 'Seeded by migration 283 (irrigation 3)'),
    ('L05', 'Drip • Center', 'center', 'drip', 'center_drips', 'drip_center', 'center_drip_clean',
     4, NULL, true, false, NULL, NULL, 30, NULL, 'system', 'migration', 'Seeded by migration 283 (irrigation 3)'),
    ('L06', 'Drip • Center (Fert.)', 'center', 'fert_drip', 'center_drips_fertilized', 'drip_center_fert',
     'center_drip_fert', 5, 'L05', true, false, NULL, NULL, 0, NULL, 'system', 'migration',
     'Seeded by migration 283 (irrigation 3)'),
    ('L07', 'Mister • South Wall', 'south', 'mister', 'south_wall_mister', 'mister_south', 'south_mister_clean',
     6, NULL, false, false, false, false, 45, 15, 'system', 'migration', 'Seeded by migration 283 (irrigation 3)'),
    ('L08', 'Mister • South Wall (Fert.)', 'south', 'fert_mister', 'south_wall_mister_fertilized',
     'mister_south_fert', 'south_mister_fert', 7, 'L07', false, false, NULL, NULL, 0, NULL, 'system', 'migration',
     'Seeded by migration 283 (irrigation 3)'),
    ('L09', 'Mister • West Wall', 'west', 'mister', 'west_wall_mister', 'mister_west', 'west_mister_clean',
     8, NULL, false, false, false, false, 45, 15, 'system', 'migration', 'Seeded by migration 283 (irrigation 3)'),
    ('L10', 'Mister • West Wall (Fert.)', 'west', 'fert_mister', 'west_wall_mister_fertilized',
     'mister_west_fert', 'west_mister_fert', 9, 'L09', false, false, NULL, NULL, 0, NULL, 'system', 'migration',
     'Seeded by migration 283 (irrigation 3)'),
    ('L11', 'Fog (AquaFog XE 2000)', 'house', 'fog', 'fog_rly', 'fog', 'center_fog',
     10, NULL, false, false, NULL, NULL, 0, NULL, 'system', 'migration', 'Seeded by migration 283 (irrigation 3)');

-- The Vanda fallback (D-IRP-12): three degraded_only windows on L04 at solar noon -120, 0 and
-- +120 min, every day, 4 pulses of 45 s, 60 s apart. They follow vpd_member, not
-- schedule_member, so L04's Schedule stays off. They equal the program of the seed vector in
-- verdify_schemas/tests/fixtures/irrigation_program_vectors.json; the sequence starts at 4.
INSERT INTO public.irrigation_slot
    (slot_id, line_id, enabled, days, start_kind, start_min, on_s, count, gap_s, prewet_s, flush_s,
     degraded_only, label, manual, source, updated_by, reason)
OVERRIDING SYSTEM VALUE
VALUES
    (1, 'L04', true, 127, 'solar_noon', -120, 45, 4, 60, 0, 0, true, 'vanda-degraded', false, 'system', 'migration',
     'Seeded by migration 283 (D-IRP-12)'),
    (2, 'L04', true, 127, 'solar_noon', 0, 45, 4, 60, 0, 0, true, 'vanda-degraded', false, 'system', 'migration',
     'Seeded by migration 283 (D-IRP-12)'),
    (3, 'L04', true, 127, 'solar_noon', 120, 45, 4, 60, 0, 0, true, 'vanda-degraded', false, 'system', 'migration',
     'Seeded by migration 283 (D-IRP-12)');

-- One row per line: every irrigation_line column, the newest relay edge of the line's equipment
-- (read as v_water_relay_runs reads it: any row on at the newest timestamp), and the newest
-- irr_state and irr_events text (NULL until the ingestor routes them). It parses nothing.
CREATE VIEW public.v_irrigation_line_state AS
SELECT l.*,
       relay.state AS relay_on,
       relay.ts AS relay_at,
       floor(extract(epoch FROM now() - relay.ts))::integer AS relay_age_s,
       irr_state.value AS irr_state,
       irr_state.ts AS irr_state_at,
       floor(extract(epoch FROM now() - irr_state.ts))::integer AS irr_state_age_s,
       irr_events.value AS irr_events,
       irr_events.ts AS irr_events_at,
       floor(extract(epoch FROM now() - irr_events.ts))::integer AS irr_events_age_s
FROM public.irrigation_line l
LEFT JOIN LATERAL (
    SELECT es.ts, bool_or(es.state) AS state
    FROM public.equipment_state es
    WHERE es.equipment = l.equipment
      AND COALESCE(es.greenhouse_id, 'vallery') = l.greenhouse_id
    GROUP BY es.ts
    ORDER BY es.ts DESC
    LIMIT 1
) relay ON true
LEFT JOIN LATERAL (
    SELECT s.ts, s.value
    FROM public.system_state s
    WHERE s.entity = 'irr_state'
      AND COALESCE(s.greenhouse_id, 'vallery') = l.greenhouse_id
    ORDER BY s.ts DESC
    LIMIT 1
) irr_state ON true
LEFT JOIN LATERAL (
    SELECT s.ts, s.value
    FROM public.system_state s
    WHERE s.entity = 'irr_events'
      AND COALESCE(s.greenhouse_id, 'vallery') = l.greenhouse_id
    ORDER BY s.ts DESC
    LIMIT 1
) irr_events ON true;
COMMENT ON VIEW public.v_irrigation_line_state IS
    'One row per irrigation line (VD-38): its irrigation_line row, its newest relay edge (relay_on, relay_at, relay_age_s) and the newest raw irr_state and irr_events text with their age. verdify_schemas.irrigation.line_state() turns a row into the API''s line fields.';

-- 281's minutes, with cause and slot from the ledger. Each run takes them from the newest
-- irrigation_ledger row on its line that started at most 24 hours before the run and whose
-- [started_at, coalesce(ended_at, now())] contains the run's start. No occurrence lasts that
-- long (a 60-pulse mist window with 600 s gaps and its 60 min tail, or a feed with its prewet,
-- flush and waits, is under 16 hours), and the bound keeps the probe on the index and keeps a row
-- whose end never arrived from claiming later runs. An unmatched L01 run that starts within 30 s
-- after the sent_at of a sent run command on L01 reads run_now (the bridge). Any other run keeps
-- 281's topology cause; zone and chemistry still come from 281's line map. Columns, owner and
-- grants are 281's.
CREATE OR REPLACE VIEW public.v_irrigation_minutes_daily AS
WITH line_map(relay_slug, line_id, zone, chemistry, cause) AS (
    VALUES
        ('drip_wall', 'L01', 'wall', 'clean', 'unknown'),
        ('drip_wall_fert', 'L02', 'wall', 'fert', 'feed'),
        ('fert_master_valve', 'L03', 'supply', 'fert', 'feed'),
        ('mister_center', 'L04', 'center', 'clean', 'vpd'),
        ('drip_center', 'L05', 'center', 'clean', 'unknown'),
        ('drip_center_fert', 'L06', 'center', 'fert', 'unknown'),
        ('mister_south', 'L07', 'south', 'clean', 'vpd'),
        ('mister_south_fert', 'L08', 'south', 'fert', 'unknown'),
        ('mister_west', 'L09', 'west', 'clean', 'vpd'),
        ('mister_west_fert', 'L10', 'west', 'fert', 'unknown'),
        ('fog', 'L11', 'fog', 'clean', 'vpd')
), master_observed AS (
    SELECT COALESCE(es.greenhouse_id, 'vallery') AS greenhouse_id,
           es.ts,
           bool_or(es.state) AS state,
           count(DISTINCT es.state) > 1 AS conflicting_state
    FROM public.equipment_state es
    WHERE es.equipment = 'fert_master_valve'
    GROUP BY COALESCE(es.greenhouse_id, 'vallery'), es.ts
), master_changed AS (
    SELECT x.greenhouse_id, x.ts, x.state
    FROM (
        SELECT o.*,
               lag(o.state) OVER (PARTITION BY o.greenhouse_id ORDER BY o.ts) AS prior_state
        FROM master_observed o
    ) x
    WHERE x.prior_state IS DISTINCT FROM x.state
       OR x.prior_state IS NULL
       OR x.conflicting_state
), master_intervals AS (
    SELECT c.*,
           lead(c.ts, 1, now()) OVER (PARTITION BY c.greenhouse_id ORDER BY c.ts) AS run_end
    FROM master_changed c
), runs AS (
    SELECT r.greenhouse_id, r.relay_slug, r.run_start, r.run_end
    FROM public.v_water_relay_runs r
    UNION ALL
    SELECT m.greenhouse_id, 'fert_master_valve'::text, m.ts, m.run_end
    FROM master_intervals m
    WHERE m.state IS TRUE
      AND m.run_end > m.ts
), attributed AS (
    SELECT r.greenhouse_id, r.relay_slug, r.run_start, r.run_end,
           COALESCE(
               ledger.cause,
               CASE WHEN l.line_id <> 'L01' THEN NULL
                    WHEN EXISTS (
                        SELECT 1 FROM public.irrigation_command c
                        WHERE c.greenhouse_id = r.greenhouse_id
                          AND c.line_id = 'L01'
                          AND c.kind = 'run'
                          AND c.status = 'sent'
                          AND c.sent_at <= r.run_start
                          AND c.sent_at >= r.run_start - interval '30 seconds'
                    ) THEN 'run_now'
               END,
               l.cause) AS cause,
           ledger.slot_id
    FROM runs r
    JOIN line_map l ON l.relay_slug = r.relay_slug
    LEFT JOIN LATERAL (
        SELECT g.cause, g.slot_id
        FROM public.irrigation_ledger g
        WHERE g.greenhouse_id = r.greenhouse_id
          AND g.line_id = l.line_id
          AND g.started_at <= r.run_start
          AND g.started_at > r.run_start - interval '24 hours'
          AND COALESCE(g.ended_at, now()) >= r.run_start
        ORDER BY g.started_at DESC, g.id DESC
        LIMIT 1
    ) ledger ON true
), pieces AS (
    SELECT a.greenhouse_id, a.relay_slug, a.cause, a.slot_id, a.run_start, a.run_end, d.local_day,
           d.local_day = (a.run_start AT TIME ZONE 'America/Denver')::date AS run_starts,
           extract(epoch FROM (least(a.run_end, d.day_end) - greatest(a.run_start, d.day_start))) AS open_s
    FROM attributed a
    CROSS JOIN LATERAL (
        SELECT s::date AS local_day,
               s::date::timestamp AT TIME ZONE 'America/Denver' AS day_start,
               (s::date + 1)::timestamp AT TIME ZONE 'America/Denver' AS day_end
        FROM generate_series((a.run_start AT TIME ZONE 'America/Denver')::date::timestamp,
                             (a.run_end AT TIME ZONE 'America/Denver')::date::timestamp,
                             interval '1 day') s
    ) d
    WHERE least(a.run_end, d.day_end) > greatest(a.run_start, d.day_start)
), metered AS (
    -- v_water_run_accounting's per-run meter_attributed_gal, read only for a
    -- run's start-day piece. An accepted delta spans at most 5 minutes (194's
    -- materializer flags longer intervals source_gap), so bounding the scan at
    -- run_end + 5 minutes keeps every overlapping delta and keeps reads bounded.
    SELECT p.*, run_gal.gal
    FROM pieces p
    LEFT JOIN LATERAL (
        SELECT CASE WHEN count(*) = 0 THEN NULL::double precision
                    ELSE round(COALESCE(sum(a.delta_gal) FILTER (
                             WHERE a.attribution_class = 'meter_attributed'), 0)::numeric, 3)::double precision
               END AS gal
        FROM public.v_water_event_attribution a
        WHERE p.run_starts
          AND p.relay_slug <> 'fert_master_valve'
          AND a.greenhouse_id = p.greenhouse_id
          AND a.ts > p.run_start
          AND a.ts <= p.run_end + interval '5 minutes'
          AND a.prior_ts < p.run_end
    ) run_gal ON true
), daily AS (
    SELECT m.greenhouse_id, m.local_day, m.relay_slug, m.cause, m.slot_id,
           sum(m.open_s) AS open_s,
           count(*) FILTER (WHERE m.run_starts) AS pulses,
           sum(m.gal) FILTER (WHERE m.run_starts) AS run_gal
    FROM metered m
    GROUP BY m.greenhouse_id, m.local_day, m.relay_slug, m.cause, m.slot_id
), coverage AS MATERIALIZED (
    SELECT c.greenhouse_id, c.local_day, c.coverage
    FROM public.v_irrigation_day_coverage c
)
SELECT d.greenhouse_id,
       d.local_day,
       l.zone,
       l.line_id,
       d.cause,
       l.chemistry,
       d.slot_id,
       round((d.open_s / 60.0)::numeric, 1)::double precision AS minutes,
       d.pulses::integer AS pulses,
       CASE
           WHEN l.line_id = 'L03' THEN NULL::integer
           WHEN d.run_gal IS NOT NULL THEN round(d.run_gal::numeric)::integer
           WHEN EXISTS (
               SELECT 1 FROM public.v_water_event_attribution a
               WHERE a.greenhouse_id = d.greenhouse_id
                 AND a.ts >= d.local_day::timestamp AT TIME ZONE 'America/Denver'
                 AND a.ts < (d.local_day + 1)::timestamp AT TIME ZONE 'America/Denver'
           ) THEN 0
           ELSE NULL::integer
       END AS gal,
       c.coverage <> 'complete' AS partial
FROM daily d
JOIN line_map l ON l.relay_slug = d.relay_slug
JOIN coverage c
  ON c.greenhouse_id = d.greenhouse_id
 AND c.local_day = d.local_day
WHERE c.coverage <> 'no_data';

COMMENT ON VIEW public.v_irrigation_minutes_daily IS
'Valve-open minutes (0.1), pulses (runs starting that day) and whole gallons per America/Denver day by greenhouse, zone, line, cause, chemistry and slot. Cause and slot_id come from the newest irrigation_ledger row on the run''s line that started at most 24 hours before it and whose span (to now while open) contains its start; an unmatched L01 run starting 0-30 s after the sent_at of a sent L01 run command reads run_now; other runs keep the line''s topology cause and a NULL slot_id. gal is NULL for L03, and when a row''s runs had no accepted delta it is 0 if the meter recorded one that day and NULL if not. partial marks a day whose coverage is not complete; no_data days have no rows.';

-- The view's rows for local days p_from..p_to, with the equipment_state scan bounded by the day
-- range: seeded with each relay's state at p_from's local midnight (read as v_water_relay_runs
-- reads it: bool_or over the newest timestamp before it), and a run still open at the end looks
-- ahead for its closing edge so its gallons match the view's. A run already on at p_from starts
-- where the view starts it (the newest conflicting timestamp since the relay was last off, else
-- the first timestamp after it), so it matches the same ledger row and bridge command.
CREATE OR REPLACE FUNCTION public.fn_irrigation_minutes_daily(p_from date, p_to date)
RETURNS TABLE (
    greenhouse_id text,
    local_day date,
    zone text,
    line_id text,
    cause text,
    chemistry text,
    slot_id integer,
    minutes double precision,
    pulses integer,
    gal integer,
    partial boolean
)
LANGUAGE sql STABLE STRICT SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $minutes$
WITH bounds AS (
    SELECT p_from::timestamp AT TIME ZONE 'America/Denver' AS from_ts,
           (p_to + 1)::timestamp AT TIME ZONE 'America/Denver' AS to_ts
), line_map(relay_slug, line_id, zone, chemistry, cause) AS (
    VALUES
        ('drip_wall', 'L01', 'wall', 'clean', 'unknown'),
        ('drip_wall_fert', 'L02', 'wall', 'fert', 'feed'),
        ('fert_master_valve', 'L03', 'supply', 'fert', 'feed'),
        ('mister_center', 'L04', 'center', 'clean', 'vpd'),
        ('drip_center', 'L05', 'center', 'clean', 'unknown'),
        ('drip_center_fert', 'L06', 'center', 'fert', 'unknown'),
        ('mister_south', 'L07', 'south', 'clean', 'vpd'),
        ('mister_south_fert', 'L08', 'south', 'fert', 'unknown'),
        ('mister_west', 'L09', 'west', 'clean', 'vpd'),
        ('mister_west_fert', 'L10', 'west', 'fert', 'unknown'),
        ('fog', 'L11', 'fog', 'clean', 'vpd')
), greenhouse_ids(greenhouse_id) AS (
    SELECT g.id FROM public.greenhouses g
    UNION
    SELECT 'vallery'::text
), seed AS (
    SELECT g.greenhouse_id, l.relay_slug,
           CASE WHEN s.state THEN COALESCE(last_conflict.ts, first_on.ts) ELSE s.ts END AS ts,
           s.state, s.conflicting_state
    FROM greenhouse_ids g
    CROSS JOIN line_map l
    CROSS JOIN bounds b
    CROSS JOIN LATERAL (
        SELECT es.ts,
               bool_or(es.state) AS state,
               count(DISTINCT es.state) > 1 AS conflicting_state
        FROM public.equipment_state es
        WHERE es.equipment = l.relay_slug
          AND COALESCE(es.greenhouse_id, 'vallery') = g.greenhouse_id
          AND es.ts < b.from_ts
        GROUP BY es.ts
        ORDER BY es.ts DESC
        LIMIT 1
    ) s
    LEFT JOIN LATERAL (
        SELECT es.ts
        FROM public.equipment_state es
        WHERE s.state
          AND es.equipment = l.relay_slug
          AND COALESCE(es.greenhouse_id, 'vallery') = g.greenhouse_id
          AND es.ts < b.from_ts
        GROUP BY es.ts
        HAVING NOT bool_or(es.state)
        ORDER BY es.ts DESC
        LIMIT 1
    ) last_off ON true
    LEFT JOIN LATERAL (
        SELECT es.ts
        FROM public.equipment_state es
        WHERE s.state
          AND es.equipment = l.relay_slug
          AND COALESCE(es.greenhouse_id, 'vallery') = g.greenhouse_id
          AND es.ts < b.from_ts
          AND (last_off.ts IS NULL OR es.ts > last_off.ts)
        GROUP BY es.ts
        HAVING count(DISTINCT es.state) > 1
        ORDER BY es.ts DESC
        LIMIT 1
    ) last_conflict ON true
    LEFT JOIN LATERAL (
        SELECT es.ts
        FROM public.equipment_state es
        WHERE s.state
          AND last_conflict.ts IS NULL
          AND es.equipment = l.relay_slug
          AND COALESCE(es.greenhouse_id, 'vallery') = g.greenhouse_id
          AND es.ts < b.from_ts
          AND (last_off.ts IS NULL OR es.ts > last_off.ts)
        ORDER BY es.ts
        LIMIT 1
    ) first_on ON true
), observed AS (
    SELECT COALESCE(es.greenhouse_id, 'vallery') AS greenhouse_id,
           es.equipment AS relay_slug,
           es.ts,
           bool_or(es.state) AS state,
           count(DISTINCT es.state) > 1 AS conflicting_state
    FROM public.equipment_state es
    CROSS JOIN bounds b
    WHERE es.equipment IN (
            'drip_wall', 'drip_wall_fert', 'fert_master_valve', 'mister_center',
            'drip_center', 'drip_center_fert', 'mister_south', 'mister_south_fert',
            'mister_west', 'mister_west_fert', 'fog')
      AND es.ts >= b.from_ts
      AND es.ts < b.to_ts
    GROUP BY COALESCE(es.greenhouse_id, 'vallery'), es.equipment, es.ts
), changed AS (
    SELECT x.greenhouse_id, x.relay_slug, x.ts, x.state
    FROM (
        SELECT s.*,
               lag(s.state) OVER (PARTITION BY s.greenhouse_id, s.relay_slug ORDER BY s.ts) AS prior_state
        FROM (
            SELECT greenhouse_id, relay_slug, ts, state, conflicting_state FROM seed
            UNION ALL
            SELECT greenhouse_id, relay_slug, ts, state, conflicting_state FROM observed
        ) s
    ) x
    WHERE x.prior_state IS DISTINCT FROM x.state
       OR x.prior_state IS NULL
       OR x.conflicting_state
), intervals AS (
    SELECT c.*,
           lead(c.ts) OVER (PARTITION BY c.greenhouse_id, c.relay_slug ORDER BY c.ts) AS next_ts
    FROM changed c
), runs AS (
    SELECT i.greenhouse_id, i.relay_slug, i.ts AS run_start,
           COALESCE(i.next_ts, ahead.ts, now()) AS run_end
    FROM intervals i
    CROSS JOIN bounds b
    LEFT JOIN LATERAL (
        SELECT es.ts
        FROM public.equipment_state es
        WHERE i.next_ts IS NULL
          AND i.state IS TRUE
          AND es.equipment = i.relay_slug
          AND COALESCE(es.greenhouse_id, 'vallery') = i.greenhouse_id
          AND es.ts >= b.to_ts
        GROUP BY es.ts
        HAVING NOT bool_or(es.state) OR count(DISTINCT es.state) > 1
        ORDER BY es.ts
        LIMIT 1
    ) ahead ON true
    WHERE i.state IS TRUE
      AND COALESCE(i.next_ts, ahead.ts, now()) > i.ts
), attributed AS (
    SELECT r.greenhouse_id, r.relay_slug, r.run_start, r.run_end,
           COALESCE(
               ledger.cause,
               CASE WHEN l.line_id <> 'L01' THEN NULL
                    WHEN EXISTS (
                        SELECT 1 FROM public.irrigation_command c
                        WHERE c.greenhouse_id = r.greenhouse_id
                          AND c.line_id = 'L01'
                          AND c.kind = 'run'
                          AND c.status = 'sent'
                          AND c.sent_at <= r.run_start
                          AND c.sent_at >= r.run_start - interval '30 seconds'
                    ) THEN 'run_now'
               END,
               l.cause) AS cause,
           ledger.slot_id
    FROM runs r
    JOIN line_map l ON l.relay_slug = r.relay_slug
    LEFT JOIN LATERAL (
        SELECT g.cause, g.slot_id
        FROM public.irrigation_ledger g
        WHERE g.greenhouse_id = r.greenhouse_id
          AND g.line_id = l.line_id
          AND g.started_at <= r.run_start
          AND g.started_at > r.run_start - interval '24 hours'
          AND COALESCE(g.ended_at, now()) >= r.run_start
        ORDER BY g.started_at DESC, g.id DESC
        LIMIT 1
    ) ledger ON true
), pieces AS (
    SELECT a.greenhouse_id, a.relay_slug, a.cause, a.slot_id, a.run_start, a.run_end, d.local_day,
           d.local_day = (a.run_start AT TIME ZONE 'America/Denver')::date AS run_starts,
           extract(epoch FROM (least(a.run_end, d.day_end) - greatest(a.run_start, d.day_start))) AS open_s
    FROM attributed a
    CROSS JOIN LATERAL (
        SELECT s::date AS local_day,
               s::date::timestamp AT TIME ZONE 'America/Denver' AS day_start,
               (s::date + 1)::timestamp AT TIME ZONE 'America/Denver' AS day_end
        FROM generate_series(greatest((a.run_start AT TIME ZONE 'America/Denver')::date, p_from)::timestamp,
                             least((a.run_end AT TIME ZONE 'America/Denver')::date, p_to)::timestamp,
                             interval '1 day') s
    ) d
    WHERE least(a.run_end, d.day_end) > greatest(a.run_start, d.day_start)
), metered AS (
    SELECT p.*, run_gal.gal
    FROM pieces p
    LEFT JOIN LATERAL (
        SELECT CASE WHEN count(*) = 0 THEN NULL::double precision
                    ELSE round(COALESCE(sum(a.delta_gal) FILTER (
                             WHERE a.attribution_class = 'meter_attributed'), 0)::numeric, 3)::double precision
               END AS gal
        FROM public.v_water_event_attribution a
        WHERE p.run_starts
          AND p.relay_slug <> 'fert_master_valve'
          AND a.greenhouse_id = p.greenhouse_id
          AND a.ts > p.run_start
          AND a.ts <= p.run_end + interval '5 minutes'
          AND a.prior_ts < p.run_end
    ) run_gal ON true
), daily AS (
    SELECT m.greenhouse_id, m.local_day, m.relay_slug, m.cause, m.slot_id,
           sum(m.open_s) AS open_s,
           count(*) FILTER (WHERE m.run_starts) AS pulses,
           sum(m.gal) FILTER (WHERE m.run_starts) AS run_gal
    FROM metered m
    GROUP BY m.greenhouse_id, m.local_day, m.relay_slug, m.cause, m.slot_id
), coverage AS MATERIALIZED (
    SELECT c.greenhouse_id, c.local_day, c.coverage
    FROM public.v_irrigation_day_coverage c
    WHERE c.local_day BETWEEN p_from AND p_to
)
SELECT d.greenhouse_id,
       d.local_day,
       l.zone,
       l.line_id,
       d.cause,
       l.chemistry,
       d.slot_id,
       round((d.open_s / 60.0)::numeric, 1)::double precision AS minutes,
       d.pulses::integer AS pulses,
       CASE
           WHEN l.line_id = 'L03' THEN NULL::integer
           WHEN d.run_gal IS NOT NULL THEN round(d.run_gal::numeric)::integer
           WHEN EXISTS (
               SELECT 1 FROM public.v_water_event_attribution a
               WHERE a.greenhouse_id = d.greenhouse_id
                 AND a.ts >= d.local_day::timestamp AT TIME ZONE 'America/Denver'
                 AND a.ts < (d.local_day + 1)::timestamp AT TIME ZONE 'America/Denver'
           ) THEN 0
           ELSE NULL::integer
       END AS gal,
       c.coverage <> 'complete' AS partial
FROM daily d
JOIN line_map l ON l.relay_slug = d.relay_slug
JOIN coverage c
  ON c.greenhouse_id = d.greenhouse_id
 AND c.local_day = d.local_day
WHERE c.coverage <> 'no_data'
$minutes$;

COMMENT ON FUNCTION public.fn_irrigation_minutes_daily(date, date) IS
'v_irrigation_minutes_daily for local days p_from..p_to with the equipment_state scan bounded by the range, for 30- to 400-day reads (API-IRR-10); cause and slot as the view attributes them.';

-- Grants, each listed. The dashboard and MCP logins read every irrigation table, the line state
-- and 194's per-event attribution (per-run gallons, API-IRR-11), write schedules, request
-- commands and edit a line's enable, flags, caps, pause and manual. Only the dashboard login
-- records a check: only a person does (D-IRP-07). The ingestor reads them, marks commands,
-- records pushes and outcomes, and asks whether an experiment is armed through the definer
-- function, with still no SELECT on control_experiments. Sequence USAGE goes wherever INSERT
-- does. Nothing for PUBLIC or the api login, and nothing on setpoint_changes.
REVOKE ALL ON public.irrigation_line, public.irrigation_slot, public.irrigation_command, public.irrigation_push,
    public.irrigation_ledger, public.v_irrigation_line_state FROM PUBLIC;
REVOKE ALL ON SEQUENCE public.irrigation_slot_slot_id_seq, public.irrigation_push_revision_seq,
    public.irrigation_ledger_id_seq FROM PUBLIC;
GRANT SELECT ON public.irrigation_line, public.irrigation_slot, public.irrigation_command, public.irrigation_push,
    public.irrigation_ledger, public.v_irrigation_line_state, public.v_water_event_attribution
    TO verdify_dashboard_runtime, verdify_mcp_runtime;
GRANT INSERT, UPDATE, DELETE ON public.irrigation_slot TO verdify_dashboard_runtime, verdify_mcp_runtime;
GRANT USAGE ON SEQUENCE public.irrigation_slot_slot_id_seq TO verdify_dashboard_runtime, verdify_mcp_runtime;
GRANT INSERT ON public.irrigation_command TO verdify_dashboard_runtime, verdify_mcp_runtime;
GRANT UPDATE (enabled, vpd_member, schedule_member, daily_cap_min, vpd_reserve_min, paused_until, manual,
              source, updated_by, updated_at, reason)
    ON public.irrigation_line TO verdify_dashboard_runtime, verdify_mcp_runtime;
GRANT UPDATE (checked_by, checked_at, check_note) ON public.irrigation_line TO verdify_dashboard_runtime;
GRANT SELECT ON public.irrigation_line, public.irrigation_slot, public.irrigation_command, public.irrigation_push,
    public.irrigation_ledger, public.v_irrigation_line_state TO verdify_ingestor_runtime;
GRANT UPDATE (status, sent_at, fail_reason, args) ON public.irrigation_command TO verdify_ingestor_runtime;
GRANT INSERT, UPDATE (pushed_at, confirmed_at, reject_code) ON public.irrigation_push TO verdify_ingestor_runtime;
GRANT USAGE ON SEQUENCE public.irrigation_push_revision_seq TO verdify_ingestor_runtime;
GRANT INSERT, UPDATE (ended_at, open_s, pulses, outcome, reason, event_seq)
    ON public.irrigation_ledger TO verdify_ingestor_runtime;
GRANT USAGE ON SEQUENCE public.irrigation_ledger_id_seq TO verdify_ingestor_runtime;
GRANT EXECUTE ON FUNCTION public.fn_mcp_armed_assignment_context() TO verdify_ingestor_runtime;

-- Successor seals: the grants above move all three. Both ordinary digests hash the dashboard
-- duty's OID in these tables' ACLs, so only a rolled-back native qualification run once 282 is
-- applied can measure them (tests/fixtures/283-irrigation-tables-native-qualification.json).
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'aa226e5fb29a9aa0548c597ef718236d0d83737cb23c322920977a9655958dc7' OR
        encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '2449cf2fc05464a4deedb5f78de6da6d3208c870dd9f841133d3c8fe5f0513db' OR
        encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
    THEN RAISE EXCEPTION '283 refuses an unqualified successor'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('aa226e5fb29a9aa0548c597ef718236d0d83737cb23c322920977a9655958dc7','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '283 missing api receipt'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('2449cf2fc05464a4deedb5f78de6da6d3208c870dd9f841133d3c8fe5f0513db','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '283 missing ingestor receipt'; END IF;
    UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e','hex') WHERE singleton;
    IF NOT FOUND THEN RAISE EXCEPTION '283 missing mcp receipt'; END IF;
END $successor$;
