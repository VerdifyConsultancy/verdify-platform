-- Run only against a disposable, socket-only database restored from a backup.
-- This checks logical recovery; it does not replace the physical-clone C0
-- catalog/receipt comparison, which intentionally includes database/role OIDs.
\set ON_ERROR_STOP on
\if :{?role_source}
\else
\set role_source 'rehearsal-seeded; not a paired backup globals artifact'
\endif

DO $audit$
DECLARE
  login_name text;
  duty_name text;
  role_pair text;
  role_pairs text[] := ARRAY[
    'verdify_experiment_v2_shadow_scheduler_login:verdify_experiment_shadow_scheduler',
    'verdify_experiment_v2_randomizer_login:verdify_experiment_randomizer',
    'verdify_experiment_v2_lifecycle_login:verdify_experiment_lifecycle',
    'verdify_experiment_v2_component_executor_login:verdify_experiment_component_executor',
    'verdify_experiment_v2_outcome_freezer_login:verdify_experiment_outcome_freezer',
    'verdify_experiment_v2_equipment_source_collector_login:verdify_experiment_equipment_source_collector'
  ];
BEGIN
  IF current_database() <> 'verdify_rehearsal'
     OR current_setting('listen_addresses') <> '' THEN
    RAISE EXCEPTION 'logical restore audit requires socket-only verdify_rehearsal';
  END IF;
  IF (SELECT pg_get_userbyid(datdba) FROM pg_database
      WHERE datname = current_database()) <> 'verdify' THEN
    RAISE EXCEPTION 'restored database owner is not verdify';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb')
     OR to_regclass('public.schema_migrations') IS NULL
     OR to_regclass('public.climate') IS NULL THEN
    RAISE EXCEPTION 'required restored extension, ledger or data relation is missing';
  END IF;
  IF (SELECT count(*) FROM public.schema_migrations) = 0
     OR (SELECT count(*) FROM public.climate) = 0
     OR (SELECT count(*) FROM timescaledb_information.hypertables) = 0
     OR EXISTS (SELECT 1 FROM pg_matviews WHERE NOT ispopulated) THEN
    RAISE EXCEPTION 'restored ledger, climate rows, hypertables or matviews are incomplete';
  END IF;
  FOREACH role_pair IN ARRAY role_pairs LOOP
    login_name := split_part(role_pair, ':', 1);
    duty_name := split_part(role_pair, ':', 2);
    IF NOT EXISTS (
      SELECT 1 FROM pg_roles login_role
      JOIN pg_auth_members membership ON membership.member = login_role.oid
      JOIN pg_roles duty_role ON duty_role.oid = membership.roleid
      WHERE login_role.rolname = login_name
        AND duty_role.rolname = duty_name
        AND login_role.rolcanlogin
        AND NOT login_role.rolsuper
        AND NOT login_role.rolcreaterole
        AND NOT login_role.rolcreatedb
        AND NOT membership.admin_option
    ) THEN
      RAISE EXCEPTION 'required bounded runtime login membership is missing: %', login_name;
    END IF;
    IF has_database_privilege(login_name, current_database(), 'CREATE')
       OR has_schema_privilege(login_name, 'public', 'CREATE') THEN
      RAISE EXCEPTION 'runtime login has object creation privilege: %', login_name;
    END IF;
  END LOOP;
END;
$audit$;

SELECT jsonb_build_object(
  'audit', 'logical-restore-v1',
  'database', current_database(),
  'database_owner', (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()),
  'timescaledb_version', (SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'),
  'schema_migrations', (SELECT count(*) FROM public.schema_migrations),
  'latest_migration', (SELECT filename FROM public.schema_migrations ORDER BY applied_at DESC, filename DESC LIMIT 1),
  'climate_rows', (SELECT count(*) FROM public.climate),
  'hypertables', (SELECT count(*) FROM timescaledb_information.hypertables),
  'unpopulated_matviews', (SELECT count(*) FROM pg_matviews WHERE NOT ispopulated),
  'bounded_runtime_memberships', 6,
  'role_source', :'role_source',
  'physical_clone_c0_contract', 'not evaluated'
);
