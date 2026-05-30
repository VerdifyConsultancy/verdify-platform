-- Migration 150: nutrient_recipes.salt_model / product_name columns + label the
--                already-live vanda_orchid_active row (N1 / FRT-1 DB half)
--
-- Backlog N1 (docs/backlog/verdify-unified-backlog-2026-05-29.md):
--   "INSERT vanda_orchid_active (... is_active=FALSE) ... add salt_model/product_name
--   column. Recipe present; planner reads it; no blind A/B dose math (SAF-2)."
--
-- IMPORTANT — the ROW ALREADY EXISTS (migration 145, LIVE). Migration 145 line
-- 939-949 already inserted vanda_orchid_active (crop_id=5, target_ec 0.40, N=50,
-- P=11.5, K=57.7, Ca=30.8, Mg=7.7, Fe=1.5, stock_a/b NULL, is_active=FALSE) and its
-- comment (145 line 935) explicitly DEFERRED the salt_model/product_name columns to
-- coordinator. 145 also states (line 945) its P/K are ALREADY ELEMENTAL. Therefore
-- this migration does NOT re-insert or re-chemistry the row; it only:
--   1. adds the two deferred columns, and
--   2. labels the existing row salt_model='single_salt', product_name='MSU 13-3-15'.
-- Re-running INSERT would either duplicate (no) or clobber 145's authored chemistry.
--
-- CROSS-GROUP CONTRACT (schemas group owns the field names; ALREADY LOCKED):
--   verdify_schemas/operations.py:242-243 ->
--       salt_model: Literal["two_part","single_salt"] | None
--       product_name: str | None
--   This migration's column names + the 'single_salt' value MUST match those exactly.
--   NutrientRecipe uses extra='ignore', so the new columns are tolerated by older
--   binaries. salt_model='single_salt' tells the doser "mix to target_ec, do NOT do
--   stock_a/b A/B ml/L math" (the row's stock_a/b are NULL by design) -> closes the
--   SAF-2 blind-dose risk. NULL salt_model on the 7 legacy GH-Flora rows = two-part.
--
-- DB-only, off the live control path. The row stays is_active=FALSE (dormant; no
-- dosing change until the operator confirms). No OTA, no replay-diff.
--
-- RESTARTS (CLAUDE.md rule 7): this migration does NOT touch verdify_schemas/**,
-- ingestor/entity_map.py, or mcp/server.py. The schema field PR (schemas group) lands
-- separately; when it does, bounce verdify-mcp so plan context surfaces salt_model.
-- This DB migration alone needs no restart (is_active=FALSE). Documented per the
-- 2026-04-21 staleness lesson.
--
-- TRANSACTION: this migration owns its transaction (single BEGIN/COMMIT). VALIDATE
-- ALONE in its own psql invocation.

BEGIN;

-- =====================================================================
-- 150.1  salt_model / product_name columns (additive, NULL-default)
-- =====================================================================
-- Names + types locked by verdify_schemas/operations.py:242-243. Both NULLable so
-- the 7 existing 2-part GH-Flora rows keep validating (NULL salt_model => legacy
-- two-part default for consumers).
ALTER TABLE nutrient_recipes
  ADD COLUMN IF NOT EXISTS salt_model text,
  ADD COLUMN IF NOT EXISTS product_name text;

-- Constrain salt_model to the schema's Literal set (NULL allowed = legacy two-part).
DO $ck$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'nutrient_recipes'::regclass
           AND conname = 'nutrient_recipes_salt_model_check'
    ) THEN
        ALTER TABLE nutrient_recipes
          ADD CONSTRAINT nutrient_recipes_salt_model_check
          CHECK (salt_model IS NULL OR salt_model IN ('two_part','single_salt'));
    END IF;
END
$ck$;

COMMENT ON COLUMN nutrient_recipes.salt_model IS
'Dosing model (migration 150/N1; names locked by verdify_schemas NutrientRecipe). '
'''single_salt'' = mix to target_ec, NOT A/B ml/L math (NULL stock_a/b is expected, not missing). '
'''two_part'' / NULL = legacy GH-Flora A/B recipe. Guards the SAF-2 blind-dose risk.';
COMMENT ON COLUMN nutrient_recipes.product_name IS
'Commercial product label (migration 150/N1), e.g. ''MSU 13-3-15''. Free text.';

-- =====================================================================
-- 150.2  Label the existing vanda_orchid_active row (do NOT touch its chemistry)
-- =====================================================================
-- The row was inserted by migration 145 (LIVE) with elemental P/K already chosen.
-- We only stamp salt_model/product_name on it. Idempotent: only sets where unset.
-- If the 145 row is somehow absent (fresh DB applying 150 before 145's INSERT ran),
-- this UPDATE is a harmless no-op (the row-insert is 145's responsibility, not 150's).
UPDATE nutrient_recipes
   SET salt_model   = COALESCE(salt_model, 'single_salt'),
       product_name = COALESCE(product_name, 'MSU 13-3-15')
 WHERE name = 'vanda_orchid_active'
   AND (salt_model IS NULL OR product_name IS NULL);

COMMIT;
