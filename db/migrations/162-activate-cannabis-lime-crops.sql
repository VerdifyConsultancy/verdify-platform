-- 162-activate-cannabis-lime-crops.sql
-- =============================================================================
-- Firmware v2 (#287 / contract B7): activate Cannabis (SOUTH) + Lime Tree
-- (WEST) in the crops catalog/registry.
--
-- Design: docs/design/firmware-v2-simplification-2026-06-10.md §3 (crop
-- priority model — rank: 1 Vanda/center, 2 Cannabis/south, 3 Lime/west,
-- 4 Pepper/east) and §3.4 (lime zone is a TENTATIVE move from south; design
-- assumes WEST, trivially re-pointed). Contract B7: "crops: cannabis(south),
-- lime(west) activated".
--
-- Live schema shapes (inspected on dev — a nightly prod replica — 2026-06-10):
--   crop_catalog: slug UNIQUE CHECK (^[a-z][a-z0-9_]*$); category CHECK in
--     (fruit, leafy_green, herb, flower, root, legume, brassica, ornamental,
--     tropical, vine); season CHECK in (cool, warm, hot, year_round,
--     short_day, long_day). Referenced by crops.crop_catalog_id (nullable FK,
--     ON DELETE SET NULL).
--   crops: NOT NULL columns are name, position (free text), zone (free text),
--     planted_date. zone_id (FK zones.id) and crop_catalog_id (FK
--     crop_catalog.id) are nullable — resolved here via NULL-safe scalar
--     subqueries. position_id is left NULL deliberately: the partial UNIQUE
--     index idx_crops_active_position (greenhouse_id, position_id) WHERE
--     is_active only applies when position_id IS NOT NULL.
--   zones: slugs center/east/north/south/west exist (status active).
--   Triggers: trg_crops_log_planted (AFTER INSERT -> crop_events) fires once
--     per insert; the NOT EXISTS guards keep re-runs from duplicating events.
--
-- Neither 'cannabis' nor 'citrus' existed in crop_catalog (verified on dev
-- 2026-06-10), so both catalog rows are created here first; the crops inserts
-- stay NULL-safe regardless (scalar subqueries yield NULL if absent).
--
-- planted_date note: 2026-06-10 is the system-activation/record date (the
-- firmware-v2 rollout), not a germination date.
--
-- #23 rollback-replay safety: NON-self-transactional — no top-level
-- BEGIN;/COMMIT; and no commit-forcing statement. SAFE to wrap in an outer
-- `BEGIN; ... ROLLBACK;` for rollback validation. Idempotent: ON CONFLICT
-- (slug) DO NOTHING + NOT-EXISTS-guarded inserts.
--
-- RESTARTS (CLAUDE.md rule 7): does not touch verdify_schemas/**,
-- ingestor/entity_map.py, or mcp/server.py — no restart obligation. The
-- dispatcher/API read crops per query.
--
-- ROLLBACK (documented; for a disposable validation DB, never live):
--   DELETE FROM crop_events WHERE crop_id IN (SELECT id FROM crops WHERE name IN ('Cannabis','Lime Tree'));
--   DELETE FROM crops WHERE name IN ('Cannabis','Lime Tree') AND greenhouse_id='vallery';
--   DELETE FROM crop_catalog WHERE slug IN ('cannabis','citrus');
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────
-- 162.1  crop_catalog rows for cannabis + citrus
--        default_target_vpd_low/high = band-anchor target curve extremes
--        +/- the contract half-widths (migration 161 is canonical; these
--        catalog defaults are informational).
-- ─────────────────────────────────────────────────────────────────────
INSERT INTO public.crop_catalog
    (slug, common_name, scientific_name, category, season, base_temp_f,
     default_target_vpd_low, default_target_vpd_high, notes)
VALUES
    ('cannabis', 'Cannabis in an Automated Greenhouse', 'Cannabis sativa',
     'herb', 'short_day', 50.0,
     0.57, 1.40,   -- min(mid 0.75 - 0.18) .. max(sm 1.18 + 0.22)
     'Added for the firmware-v2 rollout 2026-06-10 (contract B7). Veg/early-flower '
     'banding per crop_band_anchors (canonical); runs the SOUTH zone dry-leaning '
     'to defend against bud mold. Short-day flowering: the flower flip needs 12h '
     'uninterrupted dark (design §3.4 — light partition, independent of climate).'),
    ('citrus', 'Citrus in an Automated Greenhouse', 'Citrus x latifolia',
     'fruit', 'year_round', 55.0,
     0.41, 1.32,   -- min(mid 0.57 - 0.16) .. max(sm 1.10 + 0.22)
     'Added for the firmware-v2 rollout 2026-06-10 (contract B7). Potted lime '
     'tree; banding per crop_band_anchors (canonical).')
ON CONFLICT (slug) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────
-- 162.2  crops: Cannabis -> SOUTH
-- ─────────────────────────────────────────────────────────────────────
INSERT INTO public.crops
    (name, position, zone, planted_date, stage, count, base_temp_f,
     target_vpd_low, target_vpd_high, notes, is_active, greenhouse_id,
     zone_id, crop_catalog_id)
SELECT
    'Cannabis', 'SOUTH-FLOOR', 'south', DATE '2026-06-10', 'vegetative', 1, 50.0,
    0.75, 1.18,  -- vpd_target curve night-floor .. solar-noon (crop_band_anchors is canonical)
    'Activated for the firmware-v2 rollout 2026-06-10 (contract B7; priority rank 2 '
    'of 4, design §3). South soil sensor moved into the cannabis pot. Zone band '
    'served from crop_band_anchors (vpd_target 0.85/1.18/0.95/0.75, widths '
    '-0.18/+0.22); dry-leaning to defend bud mold.',
    true, 'vallery',
    (SELECT z.id FROM public.zones z WHERE z.slug = 'south' AND z.greenhouse_id = 'vallery'),
    (SELECT c.id FROM public.crop_catalog c WHERE c.slug = 'cannabis')
WHERE NOT EXISTS (
    SELECT 1 FROM public.crops
     WHERE name = 'Cannabis' AND zone = 'south' AND greenhouse_id = 'vallery'
);

-- ─────────────────────────────────────────────────────────────────────
-- 162.3  crops: Lime Tree -> WEST (tentative move from south, design §3.4)
--        Guard is name-only (any zone): if the lime is later re-pointed back
--        to south, a re-run must NOT insert a second active lime row.
-- ─────────────────────────────────────────────────────────────────────
INSERT INTO public.crops
    (name, position, zone, planted_date, stage, count, base_temp_f,
     target_vpd_low, target_vpd_high, notes, is_active, greenhouse_id,
     zone_id, crop_catalog_id)
SELECT
    'Lime Tree', 'WEST-FLOOR', 'west', DATE '2026-06-10', 'vegetative', 1, 55.0,
    0.57, 1.10,  -- vpd_target curve night-floor .. solar-noon (crop_band_anchors is canonical)
    'Activated for the firmware-v2 rollout 2026-06-10 (contract B7; priority rank 3 '
    'of 4, design §3). Tentative move from the south zone — design §3.4 assumes '
    'WEST; trivially re-pointed if it stays south. Zone band served from '
    'crop_band_anchors (vpd_target 0.60/1.10/0.70/0.57, widths -0.16/+0.22).',
    true, 'vallery',
    (SELECT z.id FROM public.zones z WHERE z.slug = 'west' AND z.greenhouse_id = 'vallery'),
    (SELECT c.id FROM public.crop_catalog c WHERE c.slug = 'citrus')
WHERE NOT EXISTS (
    SELECT 1 FROM public.crops
     WHERE name = 'Lime Tree' AND greenhouse_id = 'vallery'
);

-- ─────────────────────────────────────────────────────────────────────
-- 162.4  Post-insert assertion: both crops active, catalog + zone wired.
-- ─────────────────────────────────────────────────────────────────────
DO $assert$
DECLARE
    n_cannabis int;
    n_lime     int;
    n_unwired  int;
BEGIN
    SELECT count(*) INTO n_cannabis
      FROM public.crops
     WHERE name = 'Cannabis' AND zone = 'south' AND is_active AND greenhouse_id = 'vallery';
    SELECT count(*) INTO n_lime
      FROM public.crops
     WHERE name = 'Lime Tree' AND is_active AND greenhouse_id = 'vallery';
    SELECT count(*) INTO n_unwired
      FROM public.crops
     WHERE name IN ('Cannabis','Lime Tree') AND is_active AND greenhouse_id = 'vallery'
       AND (zone_id IS NULL OR crop_catalog_id IS NULL);

    IF n_cannabis <> 1 THEN
        RAISE EXCEPTION 'migration 162: expected exactly 1 active Cannabis row in south, found %', n_cannabis;
    END IF;
    IF n_lime <> 1 THEN
        RAISE EXCEPTION 'migration 162: expected exactly 1 active Lime Tree row, found %', n_lime;
    END IF;
    -- zone_id/crop_catalog_id are nullable by schema, but on this DB lineage
    -- both lookups must have resolved; fail loud rather than ship half-wired rows.
    IF n_unwired > 0 THEN
        RAISE EXCEPTION 'migration 162: % activated crop row(s) have NULL zone_id/crop_catalog_id — zones/crop_catalog lookup failed', n_unwired;
    END IF;
    RAISE NOTICE 'migration 162 OK: Cannabis(south) + Lime Tree(west) active and wired to zones + crop_catalog.';
END
$assert$;
