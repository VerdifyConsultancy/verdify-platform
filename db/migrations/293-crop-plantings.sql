-- 293 (#1327, #1328, #1329): plantings on the location tree, with layout, water coverage and history.
-- Jason, 2026-11-05 ~21:30Z: "End the stale crops. Agree with recommended design", with these corrections:
-- a zone has shelves, and a zone or a shelf holds many plantings in planters; a cell is optional and hydro cells
-- are one kind of location; the Vandas (several, count unknown) hang in the center zone; the floor pots sit under
-- the south shelves on the wall drip, which covers the east, south and west walls.
--   crops                  stays the planting (batch) row: + end_reason, source and actor. position_id is now the
--                          planting's anchor cell (its lowest-numbered open cell, NULL without one), kept by
--                          verdify_services.crops so older readers still work. No trigger changes.
--   crop_occupancy         where a planting is, over time: a zone, a shelf in it, or one cell on that shelf
--                          (composite keys keep the three consistent). An open row has no upper bound. A cell
--                          holds one planting at a time, enforced by a btree_gist exclusion over (position_id,
--                          during) that applies only where a cell is given; a zone or shelf holds any number.
--   crop_events            + request_id and detail (the cells of a move or a partial harvest).
--   shelves, positions     + wall, display_order and label_short; + layout_group (hydro rail A/B/C, south bay
--                          SE/SW), layout_row (0 is top) and layout_col (1-based, left to right from the walkway),
--                          seeded for all 96 positions (defaults D1 and D2, accepted 2026-11-05 ~21:45Z).
--   shelf_water_lines      which configured line waters each shelf; information only, nothing in control reads it.
--   v_position_current     the same columns, the occupant now read through open cell occupancy, plus the layout.
--   v_crop_planting_current one row per active planting with its zones, shelves and cells.
--   verdify_vision_runtime.crops  + zone_slugs and cells, so the vision job attributes by zone.
-- Backfill: one occupancy per existing planting at the place it was recorded (closed at cleared_at for ended
-- rows); the Vandas (crop 5) move from the CENTER-HANG-1 cell to the center_hang shelf with their count unknown;
-- crops 9, 14 and 15, ended in production at 2026-11-05 21:27:55Z, get end_reason 'reconciled'.
-- Grants: the dashboard and MCP duties write plantings through verdify_services.crops; the ingestor reads
-- occupancy for the soil-probe gate. The grants and the new columns move all three seals.
-- Its predecessor is exact291 (291 chains from 290; 292 is a gap). The rolled-back native qualification on
-- production at 291 measured the successor seals; tests/fixtures/293-crop-plantings-native-qualification.json
-- records it.
SET LOCAL search_path=pg_catalog,public,pg_temp;
LOCK TABLE public.schema_migrations,public.runtime_ordinary_login_attestation_receipts,
    public.mcp_runtime_boundary_receipt IN SHARE ROW EXCLUSIVE MODE;
DO $predecessor$
BEGIN
    IF current_user <> 'verdify'
        OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations'
            AND seq=291 AND filename='db/migrations/291-energy-daily-load-magnitude.sql'
            AND sha256='afd21ce741451d2530f78e94fb82bf1f7a3159476350cd6eb527a1543ae664ae'
            AND stamp_method='runner')
        OR EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>291)
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts)<>2
        OR (SELECT count(*) FROM public.mcp_runtime_boundary_receipt)<>1
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'b4b8499b426ef1c7abb2c6c996dee098d9aec968ddc3292d210cca70c2e77b76'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '2dcc7c99cd7cb2e80745ee1f2f05af9f5fa3dcdf0f6bbdd34e9cf1b870692806'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e'
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_api_runtime_login' AND boundary_sha256=decode('b4b8499b426ef1c7abb2c6c996dee098d9aec968ddc3292d210cca70c2e77b76','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='verdify_ingestor_runtime_login' AND boundary_sha256=decode('2dcc7c99cd7cb2e80745ee1f2f05af9f5fa3dcdf0f6bbdd34e9cf1b870692806','hex'))
        OR NOT EXISTS(SELECT 1 FROM public.mcp_runtime_boundary_receipt WHERE singleton AND boundary_sha256=decode('6cab4f0f09eef201254ba2ea858f514c9476f0a58954ed09762320cce919893e','hex'))
        OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='verdify_dashboard_runtime' AND NOT rolcanlogin)
        OR to_regclass('public.crop_occupancy') IS NOT NULL
        OR to_regclass('public.shelf_water_lines') IS NOT NULL
        OR to_regclass('public.v_crop_planting_current') IS NOT NULL
        OR to_regclass('public.irrigation_line') IS NULL
        OR to_regclass('verdify_vision_runtime.crops') IS NULL
    THEN RAISE EXCEPTION '293 refuses an unqualified predecessor'; END IF;
END $predecessor$;

CREATE EXTENSION IF NOT EXISTS btree_gist;

-- Layout: where each shelf and cell is drawn. Data, so a wrong default is a one-row update.
ALTER TABLE public.shelves
    ADD COLUMN wall text CONSTRAINT shelves_wall_check CHECK (wall IN ('north', 'east', 'south', 'west', 'center')),
    ADD COLUMN display_order smallint CONSTRAINT shelves_display_order_check CHECK (display_order >= 0),
    ADD COLUMN label_short text CONSTRAINT shelves_label_short_check CHECK (length(label_short) BETWEEN 1 AND 40),
    ADD CONSTRAINT shelves_id_zone_id_key UNIQUE (id, zone_id);
ALTER TABLE public.positions
    ADD COLUMN layout_group text CONSTRAINT positions_layout_group_check CHECK (layout_group ~ '^[A-Z]{1,3}$'),
    ADD COLUMN layout_row smallint CONSTRAINT positions_layout_row_check CHECK (layout_row >= 0),
    ADD COLUMN layout_col smallint CONSTRAINT positions_layout_col_check CHECK (layout_col >= 1),
    ADD CONSTRAINT positions_id_shelf_id_key UNIQUE (id, shelf_id);
COMMENT ON COLUMN public.positions.layout_group IS
    'Hydro rail A, B or C (columns 1-10, 11-20 and 21-30); south bay SE (bays 1-2) or SW (bays 3-4); else NULL (293).';
COMMENT ON COLUMN public.positions.layout_row IS '0 is the top row (hydro cells 1-30, top shelves), 1 the one below (293).';
COMMENT ON COLUMN public.positions.layout_col IS '1-based column, left to right as seen from the walkway (293).';

UPDATE public.shelves s SET wall = v.wall, display_order = v.display_order, label_short = v.label_short
  FROM (VALUES
        ('center_hang', 'center', 10, 'Hanging'),
        ('center_floor', 'center', 11, 'Center floor'),
        ('east_hydro', 'east', 20, 'Hydro'),
        ('east_shelf_top', 'east', 21, 'East top'),
        ('east_shelf_bottom', 'east', 22, 'East bottom'),
        ('south_shelf_top', 'south', 30, 'South top'),
        ('south_shelf_bottom', 'south', 31, 'South bottom'),
        ('south_floor', 'south', 32, 'South floor pots'),
        ('west_shelf_top', 'west', 40, 'West top'),
        ('west_shelf_bottom', 'west', 41, 'West bottom')
       ) AS v(slug, wall, display_order, label_short)
 WHERE s.slug = v.slug AND s.greenhouse_id = 'vallery';
UPDATE public.shelves SET notes = 'Floor pots along the south wall, under the south shelves, on the wall drip (L01/L02).'
 WHERE slug = 'south_floor' AND greenhouse_id = 'vallery' AND coalesce(notes, '') = '';

-- Hydro: cells 1-30 are the top row and 31-60 the bottom, left to right; rails A/B/C are columns 1-10, 11-20, 21-30.
UPDATE public.positions p
   SET layout_row = CASE WHEN m.n > 30 THEN 1 ELSE 0 END,
       layout_col = ((m.n - 1) % 30) + 1,
       layout_group = chr(65 + ((m.n - 1) % 30) / 10)
  FROM (SELECT id, substring(label FROM '^EAST-HYDRO-([0-9]+)$')::int AS n FROM public.positions) m
 WHERE p.id = m.id AND m.n BETWEEN 1 AND 60;
-- Wall shelves: T is the top row, B the bottom; the bay is the column; south bays 1-2 face SE and 3-4 SW.
UPDATE public.positions p
   SET layout_row = CASE WHEN m.tier = 'T' THEN 0 ELSE 1 END,
       layout_col = m.bay,
       layout_group = CASE WHEN m.wall = 'SOUTH' THEN CASE WHEN m.bay <= 2 THEN 'SE' ELSE 'SW' END END
  FROM (SELECT id, (regexp_match(label, '^([A-Z]+)-SHELF-([TB])([0-9]+)$'))[1] AS wall,
               (regexp_match(label, '^([A-Z]+)-SHELF-([TB])([0-9]+)$'))[2] AS tier,
               (regexp_match(label, '^([A-Z]+)-SHELF-([TB])([0-9]+)$'))[3]::int AS bay
          FROM public.positions) m
 WHERE p.id = m.id AND m.bay IS NOT NULL;
-- Hanging spots and floor pots: one row each.
UPDATE public.positions p
   SET layout_row = 0, layout_col = m.n
  FROM (SELECT id, substring(label FROM '^[A-Z]+-(?:FLOOR|HANG)-([0-9]+)$')::int AS n FROM public.positions) m
 WHERE p.id = m.id AND m.n IS NOT NULL;

-- Which configured line (irrigation_line, the relay source of truth) waters each shelf. Information only.
CREATE TABLE public.shelf_water_lines (
    shelf_id integer NOT NULL REFERENCES public.shelves (id) ON DELETE CASCADE,
    line_id  text NOT NULL REFERENCES public.irrigation_line (line_id) ON DELETE CASCADE,
    note     text CONSTRAINT shelf_water_lines_note_check CHECK (length(note) <= 500),
    PRIMARY KEY (shelf_id, line_id)
);
COMMENT ON TABLE public.shelf_water_lines IS
    'Which configured irrigation line waters each shelf (293). Information for the Crops page and agents; no control '
    'path reads it. The hydro rack is a hand-dosed recirculating loop with no line; center drip L05/L06 is reserved.';
INSERT INTO public.shelf_water_lines (shelf_id, line_id, note)
SELECT s.id, v.line_id, v.note
  FROM (VALUES
        ('east_shelf_top', 'L01', 'Wall drip'), ('east_shelf_top', 'L02', 'Wall drip, fertilizer'),
        ('east_shelf_bottom', 'L01', 'Wall drip'), ('east_shelf_bottom', 'L02', 'Wall drip, fertilizer'),
        ('south_shelf_top', 'L01', 'Wall drip'), ('south_shelf_top', 'L02', 'Wall drip, fertilizer'),
        ('south_shelf_bottom', 'L01', 'Wall drip'), ('south_shelf_bottom', 'L02', 'Wall drip, fertilizer'),
        ('south_floor', 'L01', 'Wall drip'), ('south_floor', 'L02', 'Wall drip, fertilizer'),
        ('west_shelf_top', 'L01', 'Wall drip'), ('west_shelf_top', 'L02', 'Wall drip, fertilizer'),
        ('west_shelf_bottom', 'L01', 'Wall drip'), ('west_shelf_bottom', 'L02', 'Wall drip, fertilizer'),
        ('south_shelf_top', 'L07', 'South wall misters'), ('south_shelf_top', 'L08', 'South wall misters, fertilizer'),
        ('south_shelf_bottom', 'L07', 'South wall misters'),
        ('south_shelf_bottom', 'L08', 'South wall misters, fertilizer'),
        ('south_floor', 'L07', 'South wall misters'), ('south_floor', 'L08', 'South wall misters, fertilizer'),
        ('west_shelf_top', 'L09', 'West wall misters'), ('west_shelf_top', 'L10', 'West wall misters, fertilizer'),
        ('west_shelf_bottom', 'L09', 'West wall misters'),
        ('west_shelf_bottom', 'L10', 'West wall misters, fertilizer'),
        ('center_hang', 'L04', 'Center misters, overhead'), ('center_floor', 'L04', 'Center misters, overhead')
       ) AS v(slug, line_id, note)
  JOIN public.shelves s ON s.slug = v.slug AND s.greenhouse_id = 'vallery'
  JOIN public.irrigation_line l ON l.line_id = v.line_id;

-- The wall drip covers the east, south and west walls (Jason, 2026-11-05), not only south and west.
UPDATE public.water_systems
   SET mount = 'Shared: east, south and west walls (every wall shelf and the south floor pots)',
       effectiveness_note = 'Canonical topology: the wall drip path is shared and serves the east, south and west '
           'walls; use zone_path=wall_shared in current irrigation views.'
 WHERE slug = 'wall_drip_clean';
UPDATE public.water_systems
   SET effectiveness_note = 'Canonical topology: the wall drip path is shared and serves the east, south and west '
           'walls; use zone_path=wall_shared in current irrigation views.'
 WHERE slug = 'wall_drip_fert';

-- The planting row: why and by whom it ended or was recorded. A NULL source is a row from before 293.
ALTER TABLE public.crops
    ADD COLUMN end_reason text CONSTRAINT crops_end_reason_check
        CHECK (end_reason IN ('harvested', 'failed', 'removed', 'moved_out', 'reconciled')),
    ADD COLUMN source text CONSTRAINT crops_source_check
        CHECK (source ~ '^(dashboard|iris|system|agent:[a-z0-9][a-z0-9_-]*)$'),
    ADD COLUMN actor text CONSTRAINT crops_actor_check CHECK (length(actor) BETWEEN 1 AND 64),
    ADD CONSTRAINT crops_end_reason_ended_check CHECK (end_reason IS NULL OR is_active IS FALSE);
COMMENT ON COLUMN public.crops.position_id IS
    'The planting''s anchor cell: its lowest-numbered open cell in crop_occupancy, NULL without one (293). '
    'crop_occupancy is where a planting is; this column keeps older readers working.';
ALTER TABLE public.crop_events
    ADD COLUMN request_id uuid,
    ADD COLUMN detail jsonb;

CREATE TABLE public.crop_occupancy (
    id            bigserial PRIMARY KEY,
    greenhouse_id text NOT NULL DEFAULT 'vallery' REFERENCES public.greenhouses (id),
    crop_id       integer NOT NULL REFERENCES public.crops (id) ON DELETE RESTRICT,
    zone_id       integer NOT NULL REFERENCES public.zones (id) ON DELETE RESTRICT,
    shelf_id      integer,
    position_id   integer,
    during        tstzrange NOT NULL,
    count         integer CONSTRAINT crop_occupancy_count_check CHECK (count >= 0),
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT crop_occupancy_during_check
        CHECK (NOT isempty(during) AND NOT lower_inf(during) AND lower_inc(during) AND NOT upper_inc(during)),
    CONSTRAINT crop_occupancy_cell_needs_shelf CHECK (position_id IS NULL OR shelf_id IS NOT NULL),
    CONSTRAINT crop_occupancy_shelf_in_zone FOREIGN KEY (shelf_id, zone_id)
        REFERENCES public.shelves (id, zone_id) ON DELETE RESTRICT,
    CONSTRAINT crop_occupancy_cell_on_shelf FOREIGN KEY (position_id, shelf_id)
        REFERENCES public.positions (id, shelf_id) ON DELETE RESTRICT,
    CONSTRAINT crop_occupancy_one_planting_per_cell
        EXCLUDE USING gist (position_id WITH =, during WITH &&) WHERE (position_id IS NOT NULL)
);
CREATE INDEX crop_occupancy_open_crop_idx ON public.crop_occupancy (crop_id) WHERE upper_inf(during);
CREATE INDEX crop_occupancy_open_zone_idx ON public.crop_occupancy (zone_id) WHERE upper_inf(during);
COMMENT ON TABLE public.crop_occupancy IS
    'Where a planting (crops row) is over time (293): a zone, a shelf in it, or one cell on that shelf. An open row '
    'has no upper bound. A cell holds one planting at a time; a zone or a shelf holds any number. Written only '
    'through verdify_services.crops; no control path reads it.';

-- Backfill: each recorded planting where it was recorded, from its planted date (local midnight) to cleared_at.
-- A row whose cleared_at falls before that start (a same-day test row) has no range and gets none.
INSERT INTO public.crop_occupancy (greenhouse_id, crop_id, zone_id, shelf_id, position_id, during, count)
SELECT c.greenhouse_id, c.id, sh.zone_id, p.shelf_id, p.id,
       tstzrange((c.planted_date::timestamp AT TIME ZONE 'America/Denver'), c.cleared_at, '[)'), NULL
  FROM public.crops c
  JOIN public.positions p ON p.id = c.position_id
  JOIN public.shelves sh ON sh.id = p.shelf_id
 WHERE c.id <> 5
   AND (c.cleared_at IS NULL OR c.cleared_at > (c.planted_date::timestamp AT TIME ZONE 'America/Denver'));
INSERT INTO public.crop_occupancy (greenhouse_id, crop_id, zone_id, shelf_id, position_id, during, count)
SELECT c.greenhouse_id, c.id, z.id, sh.id, NULL,
       tstzrange((c.planted_date::timestamp AT TIME ZONE 'America/Denver'), c.cleared_at, '[)'), NULL
  FROM public.crops c
  JOIN public.zones z ON z.id = c.zone_id
  LEFT JOIN public.shelves sh ON sh.zone_id = z.id AND sh.slug = lower(replace(c.position, '-', '_'))
 WHERE c.position_id IS NULL AND c.id <> 5
   AND (c.cleared_at IS NULL OR c.cleared_at > (c.planted_date::timestamp AT TIME ZONE 'America/Denver'));
-- The Vandas: several plants, count unknown, hanging in the center zone (no single cell).
INSERT INTO public.crop_occupancy (greenhouse_id, crop_id, zone_id, shelf_id, position_id, during, count)
SELECT c.greenhouse_id, c.id, sh.zone_id, sh.id, NULL,
       tstzrange((c.planted_date::timestamp AT TIME ZONE 'America/Denver'), NULL, '[)'), NULL
  FROM public.crops c
  JOIN public.shelves sh ON sh.slug = 'center_hang' AND sh.greenhouse_id = c.greenhouse_id
 WHERE c.id = 5 AND c.is_active;
UPDATE public.crops SET count = NULL, position_id = NULL, position = 'CENTER-HANG', updated_at = now()
 WHERE id = 5 AND is_active
   AND (count IS NOT NULL OR position_id IS NOT NULL OR position IS DISTINCT FROM 'CENTER-HANG');
-- Ended in production at 2026-11-05 21:27:55Z as not in the greenhouse (pepper, Cannabis, Lime Tree). Every other
-- ended row was logged as 'removed' by trg_crops_log_clear, so it keeps that word. Writing the column on each row
-- also gives it a crop_assignment_revisions row of its new shape (255's lineage compares the newest revision with
-- to_jsonb of the live row, which the new columns change); the check below holds every row to that.
UPDATE public.crops SET end_reason = 'reconciled' WHERE id IN (9, 14, 15) AND is_active IS FALSE;
UPDATE public.crops SET end_reason = 'removed' WHERE is_active IS FALSE AND end_reason IS NULL;

-- The occupant of a cell now comes from its open occupancy; the columns before is_occupied are unchanged.
CREATE OR REPLACE VIEW public.v_position_current AS
 SELECT p.id AS position_id,
    p.greenhouse_id,
    p.label AS position_label,
    sh.slug AS shelf_slug,
    sh.kind AS shelf_kind,
    z.id AS zone_id,
    z.slug AS zone_slug,
    z.name AS zone_name,
    c.id AS crop_id,
    c.name AS crop_name,
    c.variety AS crop_variety,
    c.stage AS crop_stage,
    c.planted_date AS crop_planted_date,
    c.expected_harvest AS crop_expected_harvest,
    cc.slug AS crop_catalog_slug,
    CURRENT_DATE - c.planted_date AS crop_days_in_place,
    c.id IS NOT NULL AS is_occupied,
    p.layout_group,
    p.layout_row,
    p.layout_col,
    sh.wall,
    o.count AS occupancy_count,
    lower(o.during) AS occupied_since
   FROM public.positions p
     JOIN public.shelves sh ON sh.id = p.shelf_id
     JOIN public.zones z ON z.id = sh.zone_id
     LEFT JOIN public.crop_occupancy o ON o.position_id = p.id AND upper_inf(o.during)
     LEFT JOIN public.crops c ON c.id = o.crop_id
     LEFT JOIN public.crop_catalog cc ON cc.id = c.crop_catalog_id
  WHERE p.is_active
  ORDER BY z.slug, sh.slug, p.label;

CREATE VIEW public.v_crop_planting_current AS
 SELECT c.id AS planting_id,
    c.greenhouse_id,
    c.name,
    c.variety,
    cc.slug AS crop_catalog_slug,
    c.count,
    c.stage,
    c.planted_date,
    c.source,
    c.actor,
    c.notes,
    o.zone_slugs,
    o.shelf_slugs,
    o.cells,
    o.since
   FROM public.crops c
     LEFT JOIN public.crop_catalog cc ON cc.id = c.crop_catalog_id
     LEFT JOIN LATERAL (
        SELECT array_agg(DISTINCT z.slug ORDER BY z.slug) AS zone_slugs,
               array_remove(array_agg(DISTINCT s.slug ORDER BY s.slug), NULL) AS shelf_slugs,
               array_remove(array_agg(DISTINCT p.label ORDER BY p.label), NULL) AS cells,
               min(lower(x.during)) AS since
          FROM public.crop_occupancy x
          JOIN public.zones z ON z.id = x.zone_id
          LEFT JOIN public.shelves s ON s.id = x.shelf_id
          LEFT JOIN public.positions p ON p.id = x.position_id
         WHERE x.crop_id = c.id AND upper_inf(x.during)) o ON true
  WHERE c.is_active;
COMMENT ON VIEW public.v_crop_planting_current IS
    'One row per active planting with the zones, shelves and cells of its open occupancy (293).';

-- Vision: the same columns, plus the zones and cells each active planting occupies, so an observation is
-- attributed only to a planting in a zone the camera sees.
CREATE OR REPLACE VIEW verdify_vision_runtime.crops WITH (security_barrier=true) AS
 SELECT c.id, c.name, c."position", c.zone, c.stage, c.notes, c.is_active, c.greenhouse_id, c.position_id,
    c.zone_id, o.zone_slugs, o.cells
   FROM public.crops c
     LEFT JOIN LATERAL (
        SELECT array_agg(DISTINCT z.slug ORDER BY z.slug) AS zone_slugs,
               array_remove(array_agg(DISTINCT p.label ORDER BY p.label), NULL) AS cells
          FROM public.crop_occupancy x
          JOIN public.zones z ON z.id = x.zone_id
          LEFT JOIN public.positions p ON p.id = x.position_id
         WHERE x.crop_id = c.id AND upper_inf(x.during)) o ON true
  WHERE c.is_active;

DO $layout$
BEGIN
    IF EXISTS(SELECT 1 FROM public.positions p JOIN public.shelves s ON s.id = p.shelf_id
               WHERE p.is_active AND s.kind IN ('nft', 'shelf', 'hang', 'floor')
                 AND (p.layout_row IS NULL OR p.layout_col IS NULL))
        OR EXISTS(SELECT 1 FROM public.shelves WHERE wall IS NULL OR display_order IS NULL)
        OR EXISTS(SELECT 1 FROM public.positions p JOIN public.shelves s ON s.id = p.shelf_id
                   WHERE s.kind = 'nft' AND p.is_active AND p.layout_group IS NULL)
        OR EXISTS(SELECT 1 FROM public.crops c WHERE c.is_active
                     AND NOT EXISTS(SELECT 1 FROM public.crop_occupancy o
                                     WHERE o.crop_id = c.id AND upper_inf(o.during)))
    THEN RAISE EXCEPTION '293 left a position without its layout or an active planting without a place'; END IF;
    IF to_regclass('public.crop_assignment_revisions') IS NOT NULL THEN
        IF EXISTS(SELECT 1 FROM public.crops c
                   WHERE NOT EXISTS(SELECT 1 FROM (SELECT r.operation, r.crop FROM public.crop_assignment_revisions r
                                                    WHERE r.crop_id = c.id ORDER BY r.revision_id DESC LIMIT 1) newest
                                     WHERE newest.operation <> 'delete' AND newest.crop = to_jsonb(c)))
        THEN RAISE EXCEPTION '293 left a crop whose newest assignment revision is not its live row'; END IF;
    END IF;
END $layout$;

-- The dashboard duty reads the tree and writes plantings through verdify_services.crops; the trigger on crops
-- writes its planted, stage and removed events as the caller, so both duties insert crop_events.
GRANT SELECT ON
    public.zones, public.shelves, public.positions, public.crops, public.crop_events, public.crop_occupancy,
    public.crop_catalog, public.harvests, public.shelf_water_lines, public.v_position_current,
    public.v_crop_planting_current
    TO verdify_dashboard_runtime;
GRANT INSERT ON public.crops, public.crop_occupancy, public.crop_events, public.harvests TO verdify_dashboard_runtime;
GRANT UPDATE (is_active, cleared_at, end_reason, position_id, zone_id, zone, position, count, stage, variety, notes,
              expected_harvest, updated_at)
    ON public.crops TO verdify_dashboard_runtime;
GRANT UPDATE (during) ON public.crop_occupancy TO verdify_dashboard_runtime, verdify_mcp_runtime;
GRANT USAGE ON SEQUENCE public.crops_id_seq, public.crop_occupancy_id_seq, public.crop_events_id_seq,
    public.harvests_id_seq TO verdify_dashboard_runtime;
-- MCP already writes crops, crop_events and harvests (and reads zones and the catalog); it gains the tree and
-- occupancy.
GRANT SELECT ON public.positions, public.shelves, public.crop_occupancy, public.shelf_water_lines,
    public.v_crop_planting_current TO verdify_mcp_runtime;
GRANT INSERT ON public.crop_occupancy TO verdify_mcp_runtime;
GRANT USAGE ON SEQUENCE public.crop_occupancy_id_seq TO verdify_mcp_runtime;
-- The ingestor's soil-probe gate asks which zones hold an open planting.
GRANT SELECT ON public.crop_occupancy, public.zones TO verdify_ingestor_runtime;

-- Successor seals: the new columns on relations the api login reads, the dashboard, MCP and ingestor grants and
-- the replaced view move all three digests, which hash production role OIDs, so only the rolled-back native
-- qualification can measure them. These literals are its measurement on production at 291.
DO $successor$
BEGIN
    IF encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'),'hex') IS DISTINCT FROM 'f0a0d02a96692609b37d8429085163f4ce2668431a1840f7b1635856d846a547'
        OR encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'),'hex') IS DISTINCT FROM '533b81f25b05bb544d4ecffe62d4c16c51644acff1190bbde2a85dc49130687e'
        OR encode(public.fn_mcp_runtime_boundary_digest(),'hex') IS DISTINCT FROM '2f5f7ccadf8006a351353c4fce8aec9a59f634b605adf2bcda5e6e61dbe254b9'
    THEN RAISE EXCEPTION '293 refuses an unqualified successor'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('f0a0d02a96692609b37d8429085163f4ce2668431a1840f7b1635856d846a547','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_api_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '293 missing api receipt'; END IF;
    UPDATE public.runtime_ordinary_login_attestation_receipts SET boundary_sha256=decode('533b81f25b05bb544d4ecffe62d4c16c51644acff1190bbde2a85dc49130687e','hex'), captured_at=clock_timestamp() WHERE login_name='verdify_ingestor_runtime_login';
    IF NOT FOUND THEN RAISE EXCEPTION '293 missing ingestor receipt'; END IF;
    UPDATE public.mcp_runtime_boundary_receipt SET boundary_sha256=decode('2f5f7ccadf8006a351353c4fce8aec9a59f634b605adf2bcda5e6e61dbe254b9','hex') WHERE singleton;
    IF NOT FOUND THEN RAISE EXCEPTION '293 missing mcp receipt'; END IF;
END $successor$;
