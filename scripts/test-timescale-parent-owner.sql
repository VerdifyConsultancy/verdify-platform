-- Blocking, rollback-only PG16/TimescaleDB 2.25.2 ownership regression.
-- Repair the logical parent; Timescale must carry the owner to both regular
-- chunks and the compressed companion while preserving the explicit grant.
BEGIN;
CREATE ROLE test_672_rogue NOLOGIN;
CREATE ROLE test_672_reader NOLOGIN;
CREATE TABLE public.test_672_owner_contract (
    ts timestamptz NOT NULL,
    series text NOT NULL,
    value integer,
    PRIMARY KEY (ts, series)
);
SELECT create_hypertable('public.test_672_owner_contract', 'ts',
                         chunk_time_interval => interval '1 day');
GRANT SELECT ON public.test_672_owner_contract TO test_672_reader;
INSERT INTO public.test_672_owner_contract VALUES
    (now() - interval '100 years', 'old', 1),
    (now(), 'current', 2);
ALTER TABLE public.test_672_owner_contract SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'series',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT compress_chunk(chunk_oid)
  FROM show_chunks('public.test_672_owner_contract',
                   older_than => now() - interval '99 years') chunk_oid;

-- The only owner mutation in this fixture targets the user-owned parent.
-- The internal chunk relations are inspected, never altered directly.
ALTER TABLE public.test_672_owner_contract OWNER TO test_672_rogue;
DO $rogue_owner$
BEGIN
    IF (SELECT count(*) FROM _timescaledb_catalog.chunk c
          WHERE c.hypertable_id IN (
              SELECT h.id FROM _timescaledb_catalog.hypertable h
               WHERE h.schema_name = 'public'
                 AND h.table_name = 'test_672_owner_contract'
              UNION ALL
              SELECT h.compressed_hypertable_id
                FROM _timescaledb_catalog.hypertable h
               WHERE h.schema_name = 'public'
                 AND h.table_name = 'test_672_owner_contract')
            AND NOT c.dropped) <> 3
       OR (SELECT count(*) FROM _timescaledb_catalog.chunk c
             WHERE c.hypertable_id = (
                 SELECT h.compressed_hypertable_id
                   FROM _timescaledb_catalog.hypertable h
                  WHERE h.schema_name = 'public'
                    AND h.table_name = 'test_672_owner_contract')
               AND NOT c.dropped) <> 1
       OR EXISTS (
            SELECT 1 FROM _timescaledb_catalog.chunk c
            JOIN pg_class relation ON relation.oid = to_regclass(
                format('%I.%I', c.schema_name, c.table_name))
           WHERE c.hypertable_id IN (
                SELECT h.id FROM _timescaledb_catalog.hypertable h
                 WHERE h.schema_name = 'public'
                   AND h.table_name = 'test_672_owner_contract'
                UNION ALL
                SELECT h.compressed_hypertable_id
                  FROM _timescaledb_catalog.hypertable h
                 WHERE h.schema_name = 'public'
                   AND h.table_name = 'test_672_owner_contract')
             AND NOT c.dropped
             AND relation.relowner <> 'test_672_rogue'::regrole)
       OR NOT has_table_privilege('test_672_reader',
                                  'public.test_672_owner_contract', 'SELECT') THEN
        RAISE EXCEPTION 'parent owner change did not propagate to regular and compressed chunks with ACL intact';
    END IF;
END;
$rogue_owner$;

-- REASSIGN OWNED is the supported role-normalization primitive used by
-- migration 217. Repeat it to exercise idempotence without a chunk ALTER.
DO $repair_owner$
DECLARE
    database_owner name;
BEGIN
    SELECT r.rolname INTO STRICT database_owner
      FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba
     WHERE d.datname = current_database();
    EXECUTE format('REASSIGN OWNED BY test_672_rogue TO %I', database_owner);
    EXECUTE format('REASSIGN OWNED BY test_672_rogue TO %I', database_owner);
END;
$repair_owner$;
DO $restored_owner$
BEGIN
    IF EXISTS (
        SELECT 1 FROM _timescaledb_catalog.chunk c
        JOIN pg_class relation ON relation.oid = to_regclass(
            format('%I.%I', c.schema_name, c.table_name))
       WHERE c.hypertable_id IN (
            SELECT h.id FROM _timescaledb_catalog.hypertable h
             WHERE h.schema_name = 'public'
               AND h.table_name = 'test_672_owner_contract'
            UNION ALL
            SELECT h.compressed_hypertable_id
              FROM _timescaledb_catalog.hypertable h
             WHERE h.schema_name = 'public'
               AND h.table_name = 'test_672_owner_contract')
         AND NOT c.dropped
         AND relation.relowner <> (SELECT datdba FROM pg_database
                                   WHERE datname = current_database()))
       OR (SELECT relowner FROM pg_class
            WHERE oid = 'public.test_672_owner_contract'::regclass) <>
          (SELECT datdba FROM pg_database
            WHERE datname = current_database())
       OR NOT has_table_privilege('test_672_reader',
                                  'public.test_672_owner_contract', 'SELECT') THEN
        RAISE EXCEPTION 'supported parent/REASSIGN repair did not preserve chunk ownership and ACL';
    END IF;
END;
$restored_owner$;

\echo 'PASS: Timescale parent-owned compressed and uncompressed chunks, repeated repair and ACL preservation'
ROLLBACK;
