\set ON_ERROR_STOP on

-- Verification-only contract for the operator-owned Lab reporting projection.
-- This file creates no role, schema, view, table, grant, or credential. It is
-- intended to run with the separately issued reporting reader before replicas
-- can be raised from zero.
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '10s';

SELECT
  current_database() <> 'verdify' AS isolated_database,
  current_user <> 'verdify' AS distinct_reader,
  NOT has_database_privilege(current_user, current_database(), 'CREATE') AS no_database_create,
  to_regnamespace('lab_reporting') IS NOT NULL AS reporting_schema_present,
  current_schema() = 'lab_reporting' AS reporting_search_path,
  NOT has_schema_privilege(current_user, 'lab_reporting', 'CREATE') AS no_schema_create,
  to_regclass('lab_reporting.source_watermark_v1') IS NOT NULL AS watermark_view_present
\gset projection_

\if :projection_isolated_database
\else
  \echo 'projection readiness failed: reporting database is not isolated'
  \quit 3
\endif
\if :projection_distinct_reader
\else
  \echo 'projection readiness failed: reporting reader reuses the Track A role'
  \quit 3
\endif
\if :projection_no_database_create
\else
  \echo 'projection readiness failed: reporting reader can create database objects'
  \quit 3
\endif
\if :projection_reporting_schema_present
\else
  \echo 'projection readiness failed: lab_reporting schema is absent'
  \quit 3
\endif
\if :projection_reporting_search_path
\else
  \echo 'projection readiness failed: unqualified dashboard queries do not resolve inside lab_reporting'
  \quit 3
\endif
\if :projection_no_schema_create
\else
  \echo 'projection readiness failed: reporting reader can create schema objects'
  \quit 3
\endif
\if :projection_watermark_view_present
\else
  \echo 'projection readiness failed: true source-watermark view is absent'
  \quit 3
\endif

WITH relations AS (
  SELECT c.oid, c.relkind
  FROM pg_class AS c
  JOIN pg_namespace AS n ON n.oid = c.relnamespace
  WHERE n.nspname = 'lab_reporting'
    AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
), privilege_summary AS (
  SELECT
    count(*) > 0 AS projection_present,
    COALESCE(bool_and(has_table_privilege(current_user, oid, 'SELECT')), false) AS all_relations_selectable,
    COALESCE(bool_and(NOT has_table_privilege(
      current_user,
      oid,
      'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
    )), false) AS no_relation_writes,
    COALESCE(bool_and(relkind IN ('v', 'm')), false) AS views_only
  FROM relations
)
SELECT projection_present, all_relations_selectable, no_relation_writes, views_only
FROM privilege_summary
\gset privileges_

\if :privileges_projection_present
\else
  \echo 'projection readiness failed: no reporting relations are present'
  \quit 3
\endif
\if :privileges_all_relations_selectable
\else
  \echo 'projection readiness failed: reporting projection is incomplete'
  \quit 3
\endif
\if :privileges_no_relation_writes
\else
  \echo 'projection readiness failed: reporting reader has write privileges'
  \quit 3
\endif
\if :privileges_views_only
\else
  \echo 'projection readiness failed: reporting schema contains non-projection relations'
  \quit 3
\endif

WITH source_watermark AS (
  SELECT feed_id, source_watermark, source_watermark_at
  FROM lab_reporting.source_watermark_v1
  WHERE feed_id = 'lab-public-v1'
  ORDER BY source_watermark_at DESC
  LIMIT 2
)
SELECT
  count(*) = 1 AS exactly_one,
  COALESCE(bool_and(feed_id = 'lab-public-v1'), false) AS fixed_feed,
  COALESCE(bool_and(source_watermark ~ '^wm_[A-Za-z0-9_-]{8,128}$'), false) AS opaque_watermark,
  COALESCE(bool_and(source_watermark_at <= clock_timestamp() + interval '60 seconds'), false) AS bounded_clock
FROM source_watermark
\gset watermark_

\if :watermark_exactly_one
\else
  \echo 'projection readiness failed: expected exactly one fixed reporting watermark row'
  \quit 3
\endif
\if :watermark_fixed_feed
\else
  \echo 'projection readiness failed: reporting watermark feed ID drifted'
  \quit 3
\endif
\if :watermark_opaque_watermark
\else
  \echo 'projection readiness failed: reporting source watermark is not opaque'
  \quit 3
\endif
\if :watermark_bounded_clock
\else
  \echo 'projection readiness failed: reporting source watermark is in the future'
  \quit 3
\endif

-- This is the exact row shape the private projection HTTP adapter must expose
-- at /v1/source-watermark. LIMIT 2 makes duplicate feed rows observable.
SELECT feed_id, source_watermark,
       to_char(source_watermark_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS source_watermark_at,
       true AS projection_read_only,
       false AS track_a_primary_credential
FROM lab_reporting.source_watermark_v1
WHERE feed_id = 'lab-public-v1'
ORDER BY source_watermark_at DESC
LIMIT 2;

ROLLBACK;
