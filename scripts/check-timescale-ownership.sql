-- Read-only guard for a paired logical restore. Timescale manages chunk and
-- compressed-chunk ownership through the logical hypertable. A direct ALTER
-- TABLE ... OWNER on an internal chunk is unsupported in TimescaleDB 2.25.2.
DO $ownership$
DECLARE
    mismatch record;
    logical_count integer;
    chunk_count integer;
    compressed_count integer;
BEGIN
    IF (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb')
       IS DISTINCT FROM '2.25.2' THEN
        RAISE EXCEPTION 'Timescale ownership contract is qualified for 2.25.2 only'
            USING HINT = 'Qualify the deployed Timescale version before accepting a restore rehearsal.';
    END IF;

    WITH objects AS (
        SELECT logical_h.schema_name AS logical_schema,
               logical_h.table_name AS logical_table,
               logical_relation.oid AS logical_oid,
               chunk.schema_name AS chunk_schema,
               chunk.table_name AS chunk_table,
               chunk_relation.oid AS chunk_oid,
               chunk_relation.relowner AS chunk_owner,
               logical_relation.relowner AS logical_owner,
               chunk.hypertable_id = logical_h.compressed_hypertable_id AS compressed
          FROM _timescaledb_catalog.hypertable logical_h
          LEFT JOIN pg_class logical_relation
            ON logical_relation.oid = to_regclass(
                format('%I.%I', logical_h.schema_name, logical_h.table_name))
          JOIN _timescaledb_catalog.chunk chunk
            ON chunk.hypertable_id = logical_h.id
            OR chunk.hypertable_id = logical_h.compressed_hypertable_id
          LEFT JOIN pg_class chunk_relation
            ON chunk_relation.oid = to_regclass(
                format('%I.%I', chunk.schema_name, chunk.table_name))
         WHERE logical_h.schema_name <> '_timescaledb_internal'
           AND NOT chunk.dropped
    )
    SELECT * INTO mismatch
      FROM objects
     WHERE logical_oid IS NULL OR chunk_oid IS NULL
        OR chunk_owner IS DISTINCT FROM logical_owner
     ORDER BY logical_schema, logical_table, chunk_schema, chunk_table
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION
            'Unsupported Timescale owner state: %.% chunk %.% owner differs from its logical hypertable',
            mismatch.logical_schema, mismatch.logical_table,
            mismatch.chunk_schema, mismatch.chunk_table
            USING HINT = 'Restore with the recorded owner. For a logical hypertable owner correction, use ALTER TABLE on the parent in the disposable restore; never alter an internal chunk directly.';
    END IF;

    SELECT count(DISTINCT logical_h.id), count(chunk.id),
           count(chunk.id) FILTER (
               WHERE chunk.hypertable_id = logical_h.compressed_hypertable_id)
      INTO logical_count, chunk_count, compressed_count
      FROM _timescaledb_catalog.hypertable logical_h
      LEFT JOIN _timescaledb_catalog.chunk chunk
        ON (chunk.hypertable_id = logical_h.id
            OR chunk.hypertable_id = logical_h.compressed_hypertable_id)
       AND NOT chunk.dropped
     WHERE logical_h.schema_name <> '_timescaledb_internal';
    RAISE NOTICE 'Timescale owner contract: version=2.25.2 logical_rows=% chunk_rows=% compressed_rows=%',
        logical_count, chunk_count, compressed_count;
END;
$ownership$;
