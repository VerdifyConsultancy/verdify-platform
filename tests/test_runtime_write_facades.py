"""Static fail-closed contract for migration-217 ordinary write facades."""

from __future__ import annotations

import ast
import hashlib
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/217-runtime-role-boundary.sql"
FIXTURE = ROOT / "db/migrations/tests/test-217-runtime-role-boundary.sql"

BASE_TO_FACADE = {
    "climate": "v_runtime_climate_write",
    "climate_action_log": "v_runtime_climate_action_log_write",
    "diagnostics": "v_runtime_diagnostics_write",
    "energy": "v_runtime_energy_write",
    "equipment_state": "v_runtime_equipment_state_write",
    "esp32_logs": "v_runtime_esp32_logs_write",
    "forecast_deviation_log": "v_runtime_forecast_deviation_log_write",
    "gpu_power": "v_runtime_gpu_power_write",
    "infra_cpu": "v_runtime_infra_cpu_write",
    "override_events": "v_runtime_override_events_write",
    "setpoint_changes": "v_runtime_setpoint_changes_write",
    "setpoint_clamps": "v_runtime_setpoint_clamps_write",
    "setpoint_plan": "v_runtime_setpoint_plan_write",
    "setpoint_snapshot": "v_runtime_setpoint_snapshot_write",
    "system_state": "v_runtime_system_state_write",
    "weather_forecast": "v_runtime_weather_forecast_write",
}

INVOKER_HELPER_CLOSURE = {
    "fn_band_setpoints(timestamptz)",
    "fn_band_trace(timestamptz,timestamptz,text)",
    "fn_band_setpoint_provenance(timestamptz,text)",
    "fn_center_band_setpoints(timestamptz)",
    "fn_compliance_pct(interval)",
    "fn_compliance_v2(interval)",
    "fn_crop_band_value(text,text,timestamptz,text,text,text)",
    "fn_current_season()",
    "fn_diurnal_interp(timestamptz,double precision,double precision)",
    "fn_dli_validity(timestamptz,text)",
    "fn_dli_proxy_lesson_invalid(text,text)",
    "fn_dli_source_invalid_reason(double precision)",
    "fn_equip_at(text,timestamptz)",
    "fn_equipment_health()",
    "fn_forecast_correction(text,numeric)",
    "fn_grade_credit(numeric,numeric,numeric,numeric,numeric)",
    "fn_heat_staging_inversion()",
    "fn_hermite_phase(double precision,double precision,double precision,double precision,double precision,double precision)",
    "fn_house_vpd_control_band(timestamptz)",
    "fn_lighting_circuit_policy(timestamptz,text)",
    "fn_lighting_lux_threshold_recommendation(timestamptz,text,interval)",
    "fn_lighting_minutes_policy(timestamptz,text)",
    "fn_lighting_policy(timestamptz,text)",
    "fn_plan_transition_audit(text,interval,interval)",
    "fn_planner_scorecard(date)",
    "fn_setpoint_at(text,timestamptz)",
    "fn_setpoint_at(text,text,timestamptz)",
    "fn_solar_altitude(timestamptz)",
    "fn_solar_phase(timestamptz)",
    "fn_solar_sunrise_hour(timestamptz)",
    "fn_solar_sunset_hour(timestamptz)",
    "fn_system_health()",
    "fn_zone_vpd_targets(timestamptz)",
    "fn_zone_band(text,timestamptz,text)",
    "fn_zone_band_grade(timestamptz,timestamptz,text)",
}

TRANSITIVE_ONLY_HELPERS = {
    "fn_center_band_setpoints(timestamptz)",
    "fn_compliance_v2(interval)",
    "fn_diurnal_interp(timestamptz,double precision,double precision)",
    "fn_dli_source_invalid_reason(double precision)",
    "fn_grade_credit(numeric,numeric,numeric,numeric,numeric)",
    "fn_hermite_phase(double precision,double precision,double precision,double precision,double precision,double precision)",
    "fn_solar_altitude(timestamptz)",
    "fn_solar_phase(timestamptz)",
    "fn_solar_sunrise_hour(timestamptz)",
    "fn_solar_sunset_hour(timestamptz)",
    "fn_zone_band(text,timestamptz,text)",
    "fn_zone_band_grade(timestamptz,timestamptz,text)",
}


def _enabled_runtime_sources() -> dict[Path, str]:
    paths = [ROOT / "api/main.py"]
    paths.extend(sorted((ROOT / "ingestor").rglob("*.py")))
    paths.extend(
        ROOT / "scripts" / name
        for name in (
            "forecast-action-engine.py",
            "greenhouse-quiet-mode.py",
            "ha-sensor-sync.py",
            "setpoint-server.py",
            "tempest-sync.py",
        )
    )
    return {path: path.read_text() for path in paths}


SQL_METHODS = {
    "execute",
    "executemany",
    "fetch",
    "fetchrow",
    "fetchval",
    "cursor",
    "prepare",
}


def _asyncpg_sql_literals(path: Path, source: str) -> list[str]:
    """Return SQL-looking literal first arguments, excluding prose/docstrings."""

    literals: list[str] = []
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Attribute)
            or node.func.attr not in SQL_METHODS
            or not node.args
        ):
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            literals.append(first.value)
        elif isinstance(first, ast.JoinedStr):
            literals.append(ast.get_source_segment(source, first) or ast.unparse(first))
    return literals


def _non_docstring_literals(path: Path, source: str) -> list[str]:
    """Fail closed on assigned/delayed SQL while ignoring actual docstrings."""

    tree = ast.parse(source, filename=str(path))
    docstrings: set[int] = set()
    for owner in ast.walk(tree):
        if not isinstance(owner, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) or not owner.body:
            continue
        first = owner.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


def test_all_sixteen_facades_are_exactly_mapped_and_protected():
    sql = MIGRATION.read_text()
    mapping_block = sql[sql.index("DO $runtime_write_facades$") : sql.index("$runtime_write_facades$;")]
    mapped = dict(
        re.findall(
            r"\('([^']+)',\s*'(v_runtime_[^']+_write)',\s*ARRAY\[",
            mapping_block,
        )
    )
    assert mapped == BASE_TO_FACADE
    assert "facade_count <> 16" in mapping_block
    assert "pg_relation_is_updatable(view_oid, false) & 28" in sql
    assert "security_barrier=true" in sql
    assert "security_invoker=false" in sql
    assert "cardinality(v_protected_relations) <> 50" in sql
    assert "cardinality(v_protected_sequences) <> 6" in sql
    for facade in BASE_TO_FACADE.values():
        # Creation mapping, three protected closures, grants/assertions and
        # projection attestation must all carry the same closed name.
        assert sql.count(f"'{facade}'") >= 5


def test_enabled_runtime_code_has_zero_direct_dml_on_facade_bases():
    violations: list[str] = []
    combined = "\n".join(_enabled_runtime_sources().values())
    for path, source in _enabled_runtime_sources().items():
        candidates = _asyncpg_sql_literals(path, source)
        candidates.extend(_non_docstring_literals(path, source))
        for base in BASE_TO_FACADE:
            relation = rf"(?:public\.)?{re.escape(base)}\b"
            match = any(
                re.search(
                    rf"(?:\bINSERT\s+INTO\s+{relation}\s*"
                    rf"(?:\(|VALUES\b|SELECT\b|DEFAULT\b)"
                    rf"|\bUPDATE\s+{relation}"
                    rf"(?:\s+(?:AS\s+)?[a-z_][a-z0-9_]*)?\s+SET\b"
                    rf"|\bDELETE\s+FROM\s+{relation}"
                    rf"(?:\s+(?:AS\s+)?[a-z_][a-z0-9_]*)?\s*"
                    rf"(?:WHERE\b|USING\b|RETURNING\b|;|$))",
                    candidate,
                    re.IGNORECASE,
                )
                for candidate in candidates
            )
            if match:
                violations.append(f"{path.relative_to(ROOT)}:{base}")
    assert violations == []
    for facade in BASE_TO_FACADE.values():
        assert re.search(
            rf"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
            rf"(?:public\.)?{re.escape(facade)}\b",
            combined,
            re.IGNORECASE,
        ), facade
    assert "FROM v_runtime_setpoint_changes_write" in combined
    assert "FOR UPDATE SKIP LOCKED" in combined


def test_every_runtime_view_dml_target_is_in_the_closed_contract():
    known = set(BASE_TO_FACADE.values())
    found: set[str] = set()
    for source in _enabled_runtime_sources().values():
        found.update(
            re.findall(
                r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
                r"(?:public\.)?(v_runtime_[a-z0-9_]+_write)\b",
                source,
                re.IGNORECASE,
            )
        )
    assert found == known


def test_migration_never_grants_runtime_dml_on_a_hypertable_parent():
    sql = MIGRATION.read_text()
    for base in BASE_TO_FACADE:
        assert not re.search(
            rf"GRANT\s+(?:INSERT|UPDATE|DELETE)[\s\S]{{0,1500}}?"
            rf"ON(?:\s+TABLE)?\s+public\.{re.escape(base)}\s+TO\s+"
            r"verdify_(?:api|ingestor)_runtime",
            sql,
            re.IGNORECASE,
        ), base
    assert "DROP OWNED BY %I" in sql
    acl_reset = sql[sql.index("DO $acl_reset$") : sql.index("$acl_reset$;")]
    assert "REVOKE ALL PRIVILEGES (%s)" not in acl_reset


def test_facade_column_acl_hashes_are_fixed_and_independently_recomputed():
    sql = MIGRATION.read_text()
    mapping_block = sql[sql.index("DO $runtime_write_facades$") : sql.index("$runtime_write_facades$;")]
    projections: dict[str, tuple[str, list[str]]] = {}
    for base, facade, raw_columns in re.findall(
        r"\('([^']+)',\s*'(v_runtime_[^']+_write)',\s*"
        r"ARRAY\[(.*?)\]::text\[\]\)",
        mapping_block,
        re.DOTALL,
    ):
        projections[facade] = (base, re.findall(r"'([^']+)'", raw_columns))
    assert set(projections) == set(BASE_TO_FACADE.values())

    grants: dict[tuple[str, str, str], set[str]] = {}
    for operation, raw_columns, facade, role in re.findall(
        r"GRANT\s+(INSERT|UPDATE)\s+\(([^;]+?)\)\s+ON\s+"
        r"public\.(v_runtime_[a-z0-9_]+_write)\s+TO\s+"
        r"(verdify_(?:api|ingestor)_runtime);",
        sql,
        re.DOTALL,
    ):
        grants[(facade, role, operation)] = {column.strip() for column in raw_columns.replace("\n", " ").split(",")}

    assertion_block = sql[
        sql.index("DO $runtime_write_facade_assertions$") : sql.index("$runtime_write_facade_assertions$;")
    ]
    fixed_contract = {
        facade: (base, projection_sha, insert_sha, update_sha, api_insert_sha)
        for facade, base, projection_sha, insert_sha, update_sha, api_insert_sha in re.findall(
            r"\('(v_runtime_[^']+_write)','([^']+)'"
            r",'([0-9a-f]{64})','([0-9a-f]{64})','([0-9a-f]{64})'"
            r",'([0-9a-f]{64})'\)",
            assertion_block,
        )
    }
    assert set(fixed_contract) == set(projections)

    def digest(columns: list[str]) -> str:
        return hashlib.sha256(",".join(columns).encode()).hexdigest()

    recomputed: dict[str, tuple[str, str, str, str, str]] = {}
    for facade, (base, projection) in projections.items():

        def allowed(role: str, operation: str) -> list[str]:
            granted = grants.get((facade, role, operation), set())
            assert granted <= set(projection)
            return [column for column in projection if column in granted]

        recomputed[facade] = (
            base,
            digest(projection),
            digest(allowed("verdify_ingestor_runtime", "INSERT")),
            digest(allowed("verdify_ingestor_runtime", "UPDATE")),
            digest(allowed("verdify_api_runtime", "INSERT")),
        )
    assert fixed_contract == recomputed

    fixture_contract_block = FIXTURE.read_text()
    fixture_contract = {
        facade: (base, projection_sha, insert_sha, update_sha, api_insert_sha)
        for facade, base, projection_sha, insert_sha, update_sha, api_insert_sha in re.findall(
            r"\('(v_runtime_[^']+_write)','([^']+)'"
            r",'([0-9a-f]{64})','([0-9a-f]{64})','([0-9a-f]{64})'"
            r",'([0-9a-f]{64})'\)",
            fixture_contract_block,
        )
    }
    assert fixture_contract == fixed_contract

    table_level_dml = re.findall(
        r"GRANT\s+(INSERT|UPDATE|DELETE)\s+ON(?:\s+TABLE)?\s+"
        r"public\.(v_runtime_[a-z0-9_]+_write)\s+TO\s+"
        r"(verdify_(?:api|ingestor)_runtime)",
        sql,
        re.IGNORECASE,
    )
    assert table_level_dml == [
        (
            "DELETE",
            "v_runtime_weather_forecast_write",
            "verdify_ingestor_runtime",
        )
    ]


def test_conflict_predicates_and_target_side_columns_stay_projected():
    sql = MIGRATION.read_text()
    sources = "\n".join(_enabled_runtime_sources().values())
    for column in (
        "ts",
        "parameter",
        "value",
        "source",
        "confirmed_at",
        "delivery_status",
        "expired_at",
        "superseded_by_ts",
        "planner_instance",
        "trigger_id",
        "greenhouse_id",
    ):
        assert (
            f"'{column}'"
            in sql[sql.index("('setpoint_changes'") : sql.index("('setpoint_clamps'", sql.index("('setpoint_changes'"))]
        )
    assert "ON CONFLICT (greenhouse_id, ts, host, gpu) DO UPDATE" in sources
    assert "ON CONFLICT (greenhouse_id, ts, host) DO UPDATE" in sources
    assert "INSERT INTO v_runtime_setpoint_plan_write" in sources


def test_exact_engine_fixture_is_durable_and_rollback_only():
    sql = FIXTURE.read_text()
    assert "PostgreSQL 16 / TimescaleDB 2.25.2" in sql
    assert "test_217_compressed_acl_poison" in sql
    assert "timescaledb.compress" in sql
    assert "to_regclass('public.schema_migrations') IS NULL" in sql
    assert "restored climate hypertable does not have compression enabled" in sql
    assert "compressed/current/future" in sql
    assert "ON CONFLICT (greenhouse_id, ts, host, gpu) DO UPDATE" in sql
    assert "ON CONFLICT (greenhouse_id, ts, host) DO UPDATE" in sql
    assert "current_setting('timescaledb.restoring') <> 'off'" in sql
    assert "public.forecast_action_log_id_seq" in sql
    for relation in (
        "public.v_greenhouse_now",
        "public.dli_validity_intervals",
        "public.v_system_health_score",
        "public.v_slack_crop_tasks_due",
        "public.slack_alert_runbooks",
    ):
        assert relation in sql
    for base, facade in BASE_TO_FACADE.items():
        assert f"('{facade}','{base}'" in sql
    assert "PASS: migration 217 Timescale/runtime boundary fixture" in sql
    assert sql.rstrip().endswith("ROLLBACK;")


def test_fixture_executes_every_granted_pure_read_helper_per_exact_login():
    migration = MIGRATION.read_text()
    helper_grants = migration[
        migration.index("-- Pure/read helper functions used by ordinary call sites.") : migration.index(
            "-- Effective relation/column/sequence grants"
        )
    ]
    fixture = FIXTURE.read_text()

    grant_calls: dict[str, Counter[str]] = {}
    for body, role in re.findall(
        r"GRANT EXECUTE ON FUNCTION(.*?)TO (verdify_(?:api|ingestor)_runtime);",
        helper_grants,
        re.DOTALL,
    ):
        grant_calls[role] = Counter(re.findall(r"public\.(fn_[a-z0-9_]+)\(", body))

    fixture_markers = {
        "verdify_api_runtime": (
            "-- API pure/read helper execution matrix begin.",
            "-- API pure/read helper execution matrix end.",
        ),
        "verdify_ingestor_runtime": (
            "-- Ingestor pure/read helper execution matrix begin.",
            "-- Ingestor pure/read helper execution matrix end.",
        ),
    }
    assert set(grant_calls) == set(fixture_markers)
    for role, (start, end) in fixture_markers.items():
        block = fixture[fixture.index(start) : fixture.index(end)]
        executed = Counter(re.findall(r"public\.(fn_[a-z0-9_]+)\(", block))
        assert executed == grant_calls[role]

    hostile_block = fixture[fixture.index("DO $hostile_pure_helper_acl$") : fixture.index("$hostile_pure_helper_acl$;")]
    hostile = Counter(re.findall(r"public\.(fn_[a-z0-9_]+)\(", hostile_block))
    assert hostile == (grant_calls["verdify_api_runtime"] | grant_calls["verdify_ingestor_runtime"])

    ingestor_block = fixture[
        fixture.index(fixture_markers["verdify_ingestor_runtime"][0]) : fixture.index(
            fixture_markers["verdify_ingestor_runtime"][1]
        )
    ]
    assert "fn_setpoint_at('temp_low', now())" in ingestor_block
    assert "fn_setpoint_at('vallery', 'temp_low', now())" in ingestor_block


def test_invoker_helper_digest_owner_and_fixture_closures_are_lockstep():
    migration = MIGRATION.read_text()
    blocks = re.findall(
        r"(?:v_)?invoker_helper_closure(?:\s+regprocedure\[\])?\s*:=\s*"
        r"ARRAY\[(.*?)\n\s*\];",
        migration,
        re.DOTALL,
    )
    assert len(blocks) == 3
    for block in blocks:
        signatures = re.findall(r"'public\.([^']+)'::regprocedure", block)
        assert len(signatures) == 35
        assert set(signatures) == INVOKER_HELPER_CLOSURE

    fixture = FIXTURE.read_text()
    fixture_closure = fixture[
        fixture.index("DO $exact_invoker_helper_closure$") : fixture.index("$exact_invoker_helper_closure$;")
    ]
    fixture_signatures = re.findall(
        r"'public\.([^']+)'::regprocedure",
        fixture_closure[: fixture_closure.index("transitive_only_helpers")],
    )
    assert len(fixture_signatures) == 35
    assert set(fixture_signatures) == INVOKER_HELPER_CLOSURE

    assert "cardinality(v_invoker_helper_closure) <> 35" in migration
    assert "cardinality(invoker_helper_closure) <> 35" in migration
    assert "cardinality(api_pure_read_helpers) <> 12" in migration
    assert "p.oid = ANY (invoker_helper_closure)" in migration
    assert "pg_catalog.pg_get_functiondef(procedure_row.oid)" in migration
    assert "invoker helper owner/definer closure is not exact" in migration

    transitive_blocks = re.findall(
        r"transitive_only_helpers(?:\s+regprocedure\[\])?\s*:=\s*"
        r"ARRAY\[(.*?)\n\s*\];",
        migration,
        re.DOTALL,
    )
    assert len(transitive_blocks) == 2
    for block in transitive_blocks:
        signatures = re.findall(r"'public\.([^']+)'::regprocedure", block)
        assert len(signatures) == 12
        assert set(signatures) == TRANSITIVE_ONLY_HELPERS
    fixture_transitive = fixture_closure[fixture_closure.index("transitive_only_helpers") :]
    fixture_transitive_signatures = re.findall(r"'public\.([^']+)'::regprocedure", fixture_transitive)
    assert len(fixture_transitive_signatures) == 12
    assert set(fixture_transitive_signatures) == TRANSITIVE_ONLY_HELPERS

    hostile_transitive = fixture[
        fixture.index("DO $hostile_transitive_helper_acl$") : fixture.index("$hostile_transitive_helper_acl$;")
    ]
    hostile_transitive_signatures = re.findall(r"'public\.([^']+)'::regprocedure", hostile_transitive)
    assert len(hostile_transitive_signatures) == 12
    assert set(hostile_transitive_signatures) == TRANSITIVE_ONLY_HELPERS
    assert "transitive-only helper compatibility ACL is not exact" in migration
    refresh_wrapper = migration[
        migration.index("CREATE OR REPLACE FUNCTION public.fn_runtime_refresh_materialized_views()") : migration.index(
            "-- Recreate the sole ordinary-readable v2 operational projection"
        )
    ]
    assert "SET search_path = pg_catalog, public, pg_temp" in refresh_wrapper
    assert "search_path=pg_catalog, public, pg_temp" in migration

    for marker in (
        "test_217_attest_invoker_body",
        "test_217_attest_invoker_owner",
        "test_217_attest_invoker_acl",
        "test_217_attest_invoker_definition",
        "test_217_attest_nonclosure_invoker",
        "test_217_attest_refresh_wrapper_path",
        "ALTER FUNCTION public.fn_solar_phase(timestamptz) OWNER TO test_217_rogue",
        "CREATE FUNCTION pg_temp.fn_current_season()",
        "public schema is not sealed before refresh wrapper",
    ):
        assert marker in fixture
