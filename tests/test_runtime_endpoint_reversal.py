"""Endpoint/identity fences and credential-free transient transport regression."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "endpoint_reversal", Path(__file__).parents[1] / "scripts/qualify-runtime-endpoint-reversal.py"
)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def test_refuses_production_route_and_historical_admission():
    endpoints = {
        c: dict(
            cluster_uid=u,
            database_oid=16447,
            host=c + "-rw.verdify-db-rehearsal.svc.cluster.local",
            new275_admission_installed=True,
            full_data_sequence_seal=True,
            admission_sha256="a" * 64,
            installation_sha256="b" * 64,
        )
        for c, u in m.CLUSTERS.items()
    }
    b = dict(schema="cnpg-current275-nine-endpoint-reversal-v1", endpoints=endpoints)
    m.checked_endpoints(b)
    endpoints["verdify-cnpg-s2"]["host"] = "verdify-db.verdify-prod.svc"
    with pytest.raises(RuntimeError):
        m.checked_endpoints(b)


@pytest.mark.parametrize("value,expected", [("15s", 15000), ("30000", 30000), ("0", 0), ("1min", 60000)])
def test_actual_timeout_units(value, expected):
    assert m.timeout_ms(value) == expected


@pytest.mark.parametrize(
    "error",
    [
        ConnectionRefusedError(),
        TimeoutError(),
        OSError(113, "unreachable"),
        OSError(104, "reset"),
        m.socket.gaierror(-3, "temporary"),
    ],
)
def test_transient_socket_failures_retry_without_credentials(monkeypatch, error):
    calls = []

    class Open:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def connect(address, timeout):
        calls.append(address)
        if len(calls) == 1:
            raise error
        return Open()

    monkeypatch.setattr(m.socket, "create_connection", connect)
    monkeypatch.setattr(m.time, "sleep", lambda _: None)
    m.wait_transport("isolated-host")
    assert calls == [("isolated-host", 5432)] * 2


def test_ast_constructor_uses_only_source_call_and_refuses_ambiguity(tmp_path):
    p = tmp_path / "consumer.py"
    p.write_text(
        "async def main():\n    dangerous_service_loop()\n    await asyncpg.create_pool(DB_DSN,min_size=2,max_size=10)\n"
    )

    class Driver:
        async def create_pool(self, *a, **k):
            return (a, k)

    import asyncio

    coro, sha = m.source_constructor(p, "ingestor", {"asyncpg": Driver(), "DB_DSN": "isolated-fenced-dsn"})
    args, kwargs = asyncio.run(coro)
    assert args == ("isolated-fenced-dsn",) and kwargs == {"min_size": 2, "max_size": 10} and len(sha) == 64
    p.write_text(p.read_text() + "\nasync def second():\n    await asyncpg.create_pool(DB_DSN)\n")
    with pytest.raises(RuntimeError):
        m.source_constructor(p, "ingestor", {})


def test_denials_include_real_unrelated_dml_and_cross_role():
    for role in (*m.ROLES, "grafana"):
        denied = m.deny_cases(role)
        assert denied["unrelated_dml_plan"] == "EXPLAIN (FORMAT JSON) DELETE FROM public.schema_migrations WHERE FALSE"
        assert set(denied) == {"mapping", "reveal", "owner", "unrelated_dml_plan", "cross_workload"}
        assert denied["cross_workload"] != "SET ROLE verdify_" + role + "_runtime"
    assert 'error.sqlstate == "42501"' in Path(m.__file__).read_text()
    assert '"25006"' not in Path(m.__file__).read_text()


def test_owning_api_pool_closes_when_lifespan_attestation_fails(tmp_path, monkeypatch):
    import asyncio
    import sys
    import types

    source = tmp_path / "owning.py"
    source.write_text("owning source")

    class Pool:
        closed = False

        async def close(self):
            self.closed = True

    pool = Pool()

    class Context:
        async def __aenter__(self):
            raise RuntimeError("attestation refused")

    consumer = types.SimpleNamespace(pool=pool, app=object(), lifespan=lambda _: Context())
    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace())
    monkeypatch.setattr(m, "load_module", lambda *a: consumer)
    for name in ("VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD", "OPENAI_API_KEY", "ESP32_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    profile = {"module_path": str(source), "module_sha256": m.digest(source.read_bytes())}
    with pytest.raises(RuntimeError, match="attestation refused"):
        asyncio.run(m.async_probe("api", profile, {"host": "isolated"}, "qualification-only-password", "before"))
    assert pool.closed


def identity():
    return dict(
        current_user="verdify_api_runtime_login",
        session_user="verdify_api_runtime_login",
        database="verdify_rehearsal",
        database_oid=16447,
        cluster_name="verdify-cnpg-s2",
        backend_address="10.42.3.106",
        replica=False,
        default_read_only="on",
        transaction_read_only="on",
        search_path="pg_catalog, public, pg_temp",
        elevated=False,
        memberships=["verdify_api_runtime"],
        statement_timeout="15s",
        ledger_delete_allowed=False,
    )


@pytest.mark.parametrize("state", ("42501", "25006"))
def test_readonly_denial_never_substitutes_for_privilege_denial(monkeypatch, state):
    import asyncio

    class Denied(Exception):
        sqlstate = state

    class Conn:
        async def fetchrow(self, sql):
            return identity()

        async def fetch(self, sql):
            if sql == m.HOT["api"]:
                return []
            raise Denied()

    monkeypatch.setattr(m, "async_error_types", lambda: (Denied,))
    endpoint = {"cluster": "verdify-cnpg-s2", "primary_ip": "10.42.3.106"}
    if state == "25006":
        with pytest.raises(RuntimeError, match="privilege denial"):
            asyncio.run(m.run_async_queries(Conn(), "api", endpoint))
    else:
        result = asyncio.run(m.run_async_queries(Conn(), "api", endpoint))
        assert len(result["deny"]) == 5 and all(d["sqlstate"] == "42501" for d in result["deny"])


@pytest.mark.parametrize(
    "key,value",
    [
        ("backend_address", "10.42.99.99"),
        ("elevated", True),
        ("memberships", ["verdify"]),
        ("statement_timeout", "0"),
        ("statement_timeout", "31s"),
        ("default_read_only", "off"),
        ("search_path", "public"),
    ],
)
def test_actual_backend_role_timeout_and_readonly_are_mandatory(key, value):
    actual = identity()
    actual[key] = value
    with pytest.raises(RuntimeError):
        m.checked_identity(actual, "api", {"cluster": "verdify-cnpg-s2", "primary_ip": "10.42.3.106"})


def test_native_postgres16_semantics_keep_readonly_and_distinguish_denials():
    import json

    receipt = json.loads(
        (Path(__file__).parent / "fixtures/runtime-endpoint-explain-readonly-receipt.json").read_text()
    )
    assert receipt["complete"] and receipt["scope"] == "disposable local PostgreSQL16 only"
    assert receipt["explain"]["sqlstate"] == "42501" and receipt["direct"]["sqlstate"] == "25006"
    assert receipt["explain"]["identity"]["tx_ro"] == receipt["explain"]["identity"]["default_ro"] == "on"
    assert receipt["explain"]["identity"]["delete_privilege"] is False
    assert receipt["ordinary_product_client_acceptance"] is False and receipt["explain"]["executed_dml"] is False
    assert m.deny_cases("api")["unrelated_dml_plan"].startswith("EXPLAIN (FORMAT JSON) DELETE")
    assert "ANALYZE" not in m.deny_cases("api")["unrelated_dml_plan"]


def test_supporting_source_inventory_refuses_changed_owning_driver(tmp_path):
    source = tmp_path / "config.py"
    driver = tmp_path / "db.py"
    source.write_text("own config")
    driver.write_text("own driver")
    profile = {
        "module_path": str(source),
        "module_sha256": m.digest(source.read_bytes()),
        "supporting_source_sha256": {str(driver): m.digest(driver.read_bytes())},
    }
    m.verify_source_files(profile)
    driver.write_text("changed driver")
    with pytest.raises(RuntimeError, match="supporting source"):
        m.verify_source_files(profile)
