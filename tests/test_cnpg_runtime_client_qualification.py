"""Authenticated target-client rendering: fail closed outside the rehearsal."""

import copy
import hashlib
import importlib.util
import json
import logging
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "client_qualification", ROOT / "scripts/cnpg-runtime-client-qualification.py"
)
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
OPERAND = "sha256:8b461e37d18aa049704eb6a9cde2ba9af0f955f8d3725e921070d2450bb2f137"


@contextmanager
def isolated_probe_process_state():
    """The real probe runs in its own process; driver doubles must do likewise."""
    disabled = logging.root.manager.disable
    environment = os.environ.copy()
    path = sys.path[:]
    missing = object()
    consumer = sys.modules.get("qualified_consumer", missing)
    try:
        yield
    finally:
        logging.disable(disabled)
        os.environ.clear()
        os.environ.update(environment)
        sys.path[:] = path
        if consumer is missing:
            sys.modules.pop("qualified_consumer", None)
        else:
            sys.modules["qualified_consumer"] = consumer


@pytest.fixture(autouse=True)
def probe_process_state():
    with isolated_probe_process_state():
        yield


def test_probe_process_state_restores_logging_environment_path_and_module(caplog):
    before_environment = os.environ.copy()
    before_path = sys.path[:]
    before_module = sys.modules.get("qualified_consumer")
    with isolated_probe_process_state():
        logging.disable(logging.CRITICAL)
        os.environ["DB_DSN"] = "synthetic-fixture-only"
        sys.path.insert(0, "/synthetic-image-path")
        sys.modules["qualified_consumer"] = object()
    assert os.environ == before_environment
    assert sys.path == before_path
    assert sys.modules.get("qualified_consumer") is before_module
    with caplog.at_level(logging.WARNING):
        logging.getLogger("probe-isolation-regression").warning("logging remains enabled")
    assert "logging remains enabled" in caplog.text


def binding():
    cluster_uid = "e11f1014-a77e-4ccf-9d97-e8cf5c037484"
    return {
        "cluster": {
            "metadata": {"name": client.CLUSTER, "namespace": client.NS, "uid": cluster_uid},
            "spec": {"imageName": "registry.vallery.net/operand@" + OPERAND},
            "status": {"currentPrimary": "verdify-cnpg-rehearsal-1"},
        },
        "pod": {
            "metadata": {
                "name": "verdify-cnpg-rehearsal-1",
                "namespace": client.NS,
                "uid": "17cfb795-27c3-4d68-a70b-cecc5feafa6c",
                "labels": {"cnpg.io/cluster": client.CLUSTER},
                "ownerReferences": [{"kind": "Cluster", "uid": cluster_uid}],
            },
            "status": {
                "podIP": "10.42.3.7",
                "containerStatuses": [{"name": "postgres", "ready": True, "imageID": "operand@" + OPERAND}],
            },
        },
        "service": {
            "metadata": {
                "name": client.CLUSTER + "-rw",
                "namespace": client.NS,
                "uid": "69d27e5e-6902-42b7-8476-503d0850e14d",
                "ownerReferences": [{"kind": "Cluster", "uid": cluster_uid}],
            },
            "spec": {
                "type": "ClusterIP",
                "selector": {"cnpg.io/cluster": client.CLUSTER, "role": "primary"},
                "ports": [{"port": 5432, "targetPort": 5432}],
            },
        },
    }


def test_s2_mode_binds_native_primary_and_exact_new_policy_secret_scope():
    facts = binding()
    target = "verdify-cnpg-s2"
    facts["cluster"]["metadata"]["name"] = target
    facts["cluster"]["status"]["currentPrimary"] = target + "-1"
    facts["pod"]["metadata"]["name"] = target + "-1"
    facts["pod"]["metadata"]["labels"]["cnpg.io/cluster"] = target
    facts["pod"]["status"].update(phase="Running", conditions=[{"type": "Ready", "status": "True"}])
    facts["service"]["metadata"]["name"] = target + "-rw"
    facts["service"]["spec"]["selector"]["cnpg.io/cluster"] = target
    job = client.render(
        facts,
        "api",
        "registry.vallery.net/verdifyconsultancy/verdify-api@sha256:" + "a" * 64,
        "b" * 40,
        hashlib.sha256((ROOT / "api/main.py").read_bytes()).hexdigest(),
        "d" * 64,
        "20261006",
        target,
    )
    labels = job["spec"]["template"]["metadata"]["labels"]
    assert labels == {
        "app.kubernetes.io/part-of": "verdify",
        "app.kubernetes.io/component": "cnpg-s2-runtime-qualification",
        "verdify.ai/qualification-target": target,
    }
    env = {e["name"]: e for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["DB_HOST"]["value"] == target + "-rw.verdify-db-rehearsal.svc.cluster.local"
    assert env["DB_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == target + "-api-client-auth"
    assert json.loads(env["QUALIFICATION_BINDING"]["value"])["target_cluster"] == target
    assert client.policies(target)[0]["spec"]["podSelector"]["matchLabels"] == labels
    facts["service"]["metadata"]["ownerReferences"][0]["uid"] = "wrong-owner"
    with pytest.raises(AssertionError):
        client.validate_binding(facts, target)


@pytest.mark.parametrize("role", list(client.ROLES))
def test_actual_consumer_only_scoped_secret_readonly_and_no_service_loop(role):
    job = client.render(
        binding(),
        role,
        "registry.vallery.net/verdifyconsultancy/verdify-" + role + "@sha256:" + "a" * 64,
        "b" * 40,
        hashlib.sha256(
            (
                ROOT / {"api": "api/main.py", "ingestor": "ingestor/ingestor.py", "mcp": "mcp/server.py"}[role]
            ).read_bytes()
        ).hexdigest(),
        "d" * 64,
        "20261001",
    )
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False and pod["restartPolicy"] == "Never"
    assert job["spec"]["backoffLimit"] == 0
    c = pod["containers"][0]
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    refs = [e for e in c["env"] if "valueFrom" in e]
    assert refs == [
        {
            "name": "DB_PASSWORD",
            "valueFrom": {
                "secretKeyRef": {"name": "verdify-cnpg-rehearsal-" + role + "-client-auth", "key": "password"}
            },
        }
    ]
    assert not any(e["name"] == "VERDIFY_GIT_SHA" for e in c["env"])
    assert c["command"] == ["python", "-I", "-c", client.WRAPPED_PROBE]
    assert "SET SESSION AUTHORIZATION" not in client.PROBE
    assert "readonly=True" in client.PROBE and "default_transaction_read_only=on" in client.PROBE
    assert "consumer.main(" not in client.PROBE and "consumer.lifespan(" not in client.PROBE
    assert "consumer._init_db_connection" in client.PROBE
    assert "consumer.attest_ordinary_ingestor_runtime_role" in client.PROBE
    assert "consumer._kpi_fanout_pool_get" in client.PROBE


@pytest.mark.parametrize(
    "change",
    [
        lambda b: b["cluster"]["metadata"].update(namespace="verdify-prod"),
        lambda b: b["cluster"]["spec"].update(imageName="unqualified:latest"),
        lambda b: b["pod"]["metadata"].update(deletionTimestamp="now"),
        lambda b: b["pod"]["metadata"]["ownerReferences"][0].update(uid="different"),
        lambda b: b["cluster"]["status"].update(currentPrimary="other"),
        lambda b: b["pod"]["status"]["containerStatuses"][0].update(ready=False),
        lambda b: b["service"]["spec"].update(type="LoadBalancer"),
        lambda b: b["service"]["spec"]["selector"].update(role="replica"),
        lambda b: b["service"]["metadata"].update(namespace="verdify-prod"),
        lambda b: b["service"]["spec"]["ports"][0].update(targetPort=6053),
    ],
)
def test_wrong_native_target_refused(change):
    facts = copy.deepcopy(binding())
    change(facts)
    with pytest.raises(ValueError):
        client.validate_binding(facts)


@pytest.mark.parametrize(
    "role,image,source,module_sha,profile_sha,suffix",
    [
        (
            "owner",
            "registry.vallery.net/verdifyconsultancy/verdify-api@sha256:" + "a" * 64,
            "b" * 40,
            "c" * 64,
            "d" * 64,
            "20261001",
        ),
        ("api", "registry.vallery.net/verdifyconsultancy/verdify-api:latest", "b" * 40, "c" * 64, "d" * 64, "20261001"),
        (
            "api",
            "registry.vallery.net/verdifyconsultancy/verdify-ingestor@sha256:" + "a" * 64,
            "b" * 40,
            "c" * 64,
            "d" * 64,
            "20261001",
        ),
        (
            "api",
            "registry.vallery.net/verdifyconsultancy/verdify-api@sha256:" + "a" * 64,
            "unknown",
            "c" * 64,
            "d" * 64,
            "20261001",
        ),
        (
            "api",
            "registry.vallery.net/verdifyconsultancy/verdify-api@sha256:" + "a" * 64,
            "b" * 40,
            "c" * 64,
            "",
            "20261001",
        ),
        (
            "api",
            "registry.vallery.net/verdifyconsultancy/verdify-api@sha256:" + "a" * 64,
            "b" * 40,
            "c" * 64,
            "d" * 64,
            "../bad",
        ),
    ],
)
def test_no_unqualified_consumer_or_profile(role, image, source, module_sha, profile_sha, suffix):
    with pytest.raises(ValueError):
        client.render(binding(), role, image, source, module_sha, profile_sha, suffix)


def test_client_isolation_never_inherits_database_egress_or_other_authority():
    egress, ingress = client.policies()
    assert egress["spec"]["ingress"] == []
    assert egress["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert egress["spec"]["egress"][0] == {
        "to": [{"podSelector": {"matchLabels": {"cnpg.io/cluster": client.CLUSTER}}}],
        "ports": [{"protocol": "TCP", "port": 5432}],
    }
    dns = egress["spec"]["egress"][1]
    assert len(dns["to"]) == 1 and set(dns["to"][0]) == {"namespaceSelector", "podSelector"}
    assert ingress["spec"]["policyTypes"] == ["Ingress"] and "egress" not in ingress["spec"]
    assert ingress["spec"]["ingress"][0]["ports"] == [{"protocol": "TCP", "port": 5432}]
    assert "cnpg-rehearsal" != egress["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/component"]
    assert "ipBlock" not in str(client.policies())
    assert "6053" not in str(client.policies()) and "443" not in str(client.policies())


def test_probe_failure_does_not_print_driver_exception_or_secret(capsys):
    # Failure before importing any consumer/driver still produces only an error category.
    with pytest.raises(SystemExit) as exc:
        exec(client.WRAPPED_PROBE, {})  # noqa: S102 — execute only the fixed qualification probe fixture
    assert exc.value.code == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "failed", "error_category": "KeyError", "probe_line": 4, "transport_attempts": []}


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "login",
        "database",
        "version",
        "cluster",
        "replica",
        "backend",
        "backend_family",
        "backend_null",
        "matching_ipv6",
        "readonly",
        "readonly_reset",
        "password",
        "close",
    ],
)
def test_probe_pool_startup_and_identity_fail_closed_without_secret_output(monkeypatch, capsys, bad):
    """Exercise the whole fixed probe with a driver double; not live auth credit."""
    import hashlib
    import importlib.abc
    import json
    import types

    secret = "synthetic-fixture-password"
    calls = []
    identity = {
        "current_user": "verdify_api_runtime_login",
        "session_user": "verdify_api_runtime_login",
        "database": "verdify_rehearsal",
        "server_version": "160013",
        "cluster_name": client.CLUSTER,
        "default_read_only": "on",
        "transaction_read_only": "on",
        "backend_address": "10.42.3.7",
        "replica": False,
    }
    changes = {
        "login": ("session_user", "verdify"),
        "database": ("database", "verdify"),
        "version": ("server_version", "160015"),
        "cluster": ("cluster_name", "prod"),
        "replica": ("replica", True),
        "backend": ("backend_address", "10.42.3.8"),
        "backend_family": ("backend_address", "2001:db8::7"),
        "backend_null": ("backend_address", None),
        "matching_ipv6": ("backend_address", "2001:db8::7"),
        "readonly": ("transaction_read_only", "off"),
    }
    if bad in changes:
        key, value = changes[bad]
        identity[key] = value

    expected_address = "2001:db8::7" if bad == "matching_ipv6" else "10.42.3.7"
    valid = bad in (None, "matching_ipv6")
    # PostgreSQL inet::text includes /32 or /128. The selected SQL must obtain
    # native host semantics while retaining the unmodified raw inet evidence.
    address = identity["backend_address"]
    identity["backend_address_raw"] = None if address is None else address + ("/128" if ":" in address else "/32")

    class Context:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, *_args):
            return None

    class Connection:
        identity_reads = 0

        async def fetchrow(self, sql):
            self.identity_reads += 1
            row = dict(identity)
            if "pg_catalog.host(inet_server_addr()) AS backend_address" not in sql:
                row["backend_address"] = row["backend_address_raw"]
            if bad == "readonly_reset" and self.identity_reads == 2:
                row["default_read_only"] = "off"
            return row

        def transaction(self, **kwargs):
            calls.append(("transaction", kwargs))
            return Context()

        async def fetch(self, _sql):
            calls.append("hot_query")
            return [types.SimpleNamespace()]

    connection = Connection()

    class Pool:
        def acquire(self):
            return Context()

        async def close(self):
            calls.append("pool_closed")
            if bad == "close":
                raise ConnectionError("close failed password=" + secret)

    async def init(_conn):
        calls.append("actual_consumer_init_callback")

    async def setup(_conn):
        calls.append("actual_consumer_setup_callback")

    class Loader(importlib.abc.Loader):
        def create_module(self, _spec):
            return None

        def exec_module(self, module):
            module._init_db_connection = init
            module._setup_db_connection = setup

    real_spec = importlib.util.spec_from_file_location
    monkeypatch.setattr(
        importlib.util, "spec_from_file_location", lambda name, path: real_spec(name, path, loader=Loader())
    )
    real_read = Path.read_bytes
    monkeypatch.setattr(
        Path, "read_bytes", lambda path: b"fixture-module" if str(path) == "/app/main.py" else real_read(path)
    )

    class ReadyWriter:
        def close(self):
            calls.append("readiness_socket_closed")

        async def wait_closed(self):
            pass

    async def ready_connection(host, port):
        assert host == client.HOST and port == 5432
        calls.append("tcp_readiness_not_authentication")
        return None, ReadyWriter()

    monkeypatch.setattr(__import__("asyncio"), "open_connection", ready_connection)

    async def create_pool(dsn, **kwargs):
        calls.append("password_tcp_pool_startup")
        assert secret in dsn and "default_transaction_read_only=on" in dsn
        if bad == "password":
            raise ConnectionError("rejected password=" + secret)
        await kwargs["init"](connection)
        await kwargs["setup"](connection)
        return Pool()

    monkeypatch.setitem(__import__("sys").modules, "asyncpg", types.SimpleNamespace(create_pool=create_pool))
    for name in ("POSTGRES_PASSWORD", "DB_PASS", "ESP32_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    facts = {
        "consumer": "api",
        "login": "verdify_api_runtime_login",
        "consumer_source": "b" * 40,
        "image_module_path": "/app/main.py",
        "consumer_module_sha256": hashlib.sha256(b"fixture-module").hexdigest(),
        "primary_address": expected_address,
        "hot_sql": "SELECT 1",
        "target_binding_sha256": "e" * 64,
        "consumer_image": "qualified-image",
        "profile_sha256": "d" * 64,
    }
    for key, value in {
        "QUALIFICATION_BINDING": json.dumps(facts),
        "DB_PASSWORD": secret,
        "DB_USER": facts["login"],
        "VERDIFY_GIT_SHA": "b" * 40,
        "DB_HOST": client.HOST,
        "DB_PORT": "5432",
        "DB_NAME": client.DATABASE,
        "VERDIFY_DEVICE_WRITE_ENABLED": "0",
    }.items():
        monkeypatch.setenv(key, value)
    if valid:
        exec(client.WRAPPED_PROBE, {})  # noqa: S102 — fixed probe under isolated driver double
    else:
        with pytest.raises(SystemExit) as exit_info:
            exec(client.WRAPPED_PROBE, {})  # noqa: S102 — fixed probe under isolated driver double
        assert exit_info.value.code == 1
    output = capsys.readouterr().out
    assert calls.count("password_tcp_pool_startup") == 1
    assert calls.count("tcp_readiness_not_authentication") == 1
    assert secret not in output and "postgresql://" not in output
    result = json.loads(output)
    assert result["status"] == ("passed" if valid else "failed")
    assert "password_tcp_pool_startup" in calls
    if bad != "password":
        assert "actual_consumer_init_callback" in calls and "pool_closed" in calls
    if valid:
        assert result["identity"]["backend_address_raw"] == identity["backend_address_raw"]
        assert result["identity"]["backend_address"] == expected_address
        assert ("transaction", {"readonly": True}) in calls and "hot_query" in calls
    elif bad == "readonly_reset":
        assert calls.count("hot_query") == 1
    elif bad == "close":
        assert calls.count("hot_query") == 2
    else:
        assert "hot_query" not in calls


def test_hot_queries_are_exact_consumer_source_and_no_arbitrary_sql():
    assert client.hot_query("api") in (ROOT / "api/main.py").read_text()
    assert client.hot_query("ingestor") in (ROOT / "ingestor/ingestor.py").read_text()
    mcp = client.hot_query("mcp")
    assert mcp in (ROOT / "mcp/server.py").read_text()
    assert "solar_w_m2" in mcp and "vpd_east" in mcp and "age_seconds" in mcp


def test_consumer_image_packaging_matches_actual_dockerfiles():
    for role, image_path, copy_line in [
        ("api", "/app/main.py", "COPY api/main.py ./main.py"),
        ("ingestor", "/app/ingestor/ingestor.py", "COPY ingestor/ /app/ingestor/"),
        ("mcp", "/app/mcp/server.py", "COPY mcp/server.py ./mcp/server.py"),
    ]:
        dockerfile = (ROOT / role / "Dockerfile").read_text()
        assert copy_line in dockerfile
        assert image_path in (ROOT / "scripts/cnpg-runtime-client-qualification.py").read_text()
        assert "ENV VERDIFY_GIT_SHA=$GIT_SHA" in dockerfile
    assert (
        "COPY --from=source-metadata /out/source-revision /etc/verdify/source-revision"
        in (ROOT / "api/Dockerfile").read_text()
    )
    assert "fallback_path.read_text().strip()" in client.PROBE
    assert "valid_baked and valid_fallback and baked != fallback" in client.PROBE
    assert "for checkout in range(2):" in client.PROBE


@pytest.mark.parametrize("role", list(client.ROLES))
def test_client_uid_matches_shipped_appuser_private_home(role):
    dockerfile = (ROOT / role / "Dockerfile").read_text()
    # A different numeric UID cannot traverse the image's private appuser HOME
    # when asyncpg resolves its default SSL paths. Keep the image's nonroot user.
    match = re.search(r"useradd -m -u (\d+) -s /usr/sbin/nologin appuser", dockerfile)
    assert match and "USER appuser" in dockerfile
    module = ROOT / {"api": "api/main.py", "ingestor": "ingestor/ingestor.py", "mcp": "mcp/server.py"}[role]
    job = client.render(
        binding(),
        role,
        "registry.vallery.net/verdifyconsultancy/verdify-" + role + "@sha256:" + "a" * 64,
        "b" * 40,
        hashlib.sha256(module.read_bytes()).hexdigest(),
        "d" * 64,
        "20261002",
    )
    pod = job["spec"]["template"]["spec"]
    assert pod["securityContext"]["runAsUser"] == int(match[1]) == 1000
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["automountServiceAccountToken"] is False
    assert pod["containers"][0]["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }


@pytest.mark.parametrize(
    "role,baked,fallback,passes",
    [
        ("api", "b" * 40, "b" * 40, True),
        ("api", "unknown", "b" * 40, True),
        ("api", "unknown", "", False),
        ("api", "b" * 40, "c" * 40, False),
        ("api", "c" * 40, "b" * 40, False),
        ("ingestor", "unknown", "b" * 40, False),
        ("mcp", "unknown", "b" * 40, False),
    ],
)
def test_actual_baked_revision_and_api_fallback_never_faked(monkeypatch, role, baked, fallback, passes):
    import types

    class RevisionFile:
        def is_file(self):
            return bool(fallback)

        def read_text(self):
            return fallback + "\n"

    monkeypatch.setenv("VERDIFY_GIT_SHA", baked)
    code = client.PROBE.split("baked = ", 1)[1].split("assert os.environ['DB_HOST']", 1)[0]
    code = "baked = " + code
    scope = {
        "role": role,
        "binding": {"consumer_source": "b" * 40},
        "os": __import__("os"),
        "pathlib": types.SimpleNamespace(Path=lambda _path: RevisionFile()),
    }
    if passes:
        exec(code, scope)  # noqa: S102 — exact fixed baked-revision probe branch
    else:
        with pytest.raises(AssertionError):
            exec(code, scope)  # noqa: S102 — exact fixed baked-revision probe branch


@pytest.mark.parametrize("exception", ["AssertionError", "RuntimeError", "ValueError"])
def test_safe_probe_location_never_formats_hostile_exception(exception, capsys):
    secret = "fixture-secret-password-and-dsn"
    code = (
        "class Hostile(" + exception + "):\n"
        "    def __str__(self):\n"
        "        raise RuntimeError('exception formatting must not execute')\n"
        "password = '" + secret + "'\n"
        "raise Hostile('postgresql://user:' + password + '@private-host/db')\n"
    )
    wrapper = client.WRAPPED_PROBE.replace(repr(client.PROBE), repr(code))
    with pytest.raises(SystemExit) as exc:
        exec(wrapper, {})  # noqa: S102 — fixed protected-wrapper hostile exception fixture
    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert secret not in output and "postgresql" not in output and "private-host" not in output
    assert json.loads(output) == {
        "status": "failed",
        "error_category": "Hostile",
        "probe_line": 5,
        "transport_attempts": [],
    }


def test_safe_probe_location_names_only_constant_probe_frame(capsys):
    code = "exec(compile(\"raise AssertionError('fixture-private')\", '/private/module.py', 'exec'))"
    wrapper = client.WRAPPED_PROBE.replace(repr(client.PROBE), repr(code))
    with pytest.raises(SystemExit):
        exec(wrapper, {})  # noqa: S102 — fixed wrapper excludes imported traceback frames
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": "failed", "error_category": "AssertionError", "probe_line": 1, "transport_attempts": []}


def test_safe_probe_location_uses_deepest_nested_async_probe_frame(capsys):
    code = (
        "import asyncio\n"
        "async def inner():\n"
        "    assert False, 'private-dsn-password'\n"
        "async def outer():\n"
        "    await inner()\n"
        "asyncio.run(outer())\n"
    )
    wrapper = client.WRAPPED_PROBE.replace(repr(client.PROBE), repr(code))
    with pytest.raises(SystemExit):
        exec(wrapper, {})  # noqa: S102 — native async traceback order/redaction fixture
    output = capsys.readouterr().out
    assert "private-dsn-password" not in output
    assert json.loads(output) == {
        "status": "failed",
        "error_category": "AssertionError",
        "probe_line": 3,
        "transport_attempts": [],
    }


def readiness_scope(monkeypatch, outcomes):
    import ast
    import asyncio
    import datetime
    import types

    clock = [0.0]
    calls = []

    class Writer:
        def close(self):
            calls.append("closed")

        async def wait_closed(self):
            pass

    async def connect(host, port):
        calls.append((host, port))
        value = outcomes.pop(0) if outcomes else ConnectionRefusedError("fixture-private-password")
        if isinstance(value, BaseException):
            clock[0] += 1.0
            raise value
        return None, Writer()

    async def wait_for(awaitable, timeout):
        assert 0 < timeout <= 1.0
        return await awaitable

    async def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setenv("DB_HOST", client.HOST)
    monkeypatch.setenv("DB_PORT", "5432")
    scope = {
        "asyncio": types.SimpleNamespace(
            get_running_loop=lambda: types.SimpleNamespace(time=lambda: clock[0]),
            open_connection=connect,
            wait_for=wait_for,
            sleep=sleep,
        ),
        "os": os,
        "datetime": datetime,
        "TRANSPORT_ATTEMPTS": [],
        "EXPECTED_TRANSPORT_HOST": client.HOST,
    }
    node = next(
        n for n in ast.parse(client.PROBE).body if isinstance(n, ast.AsyncFunctionDef) and n.name == "wait_transport"
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<selected-readiness>", "exec"), scope)  # noqa: S102 — selected fixed source function only
    return scope, calls, clock, asyncio


def test_readiness_refusal_then_success_retains_attempts_and_closes(monkeypatch):
    scope, calls, _, asyncio = readiness_scope(monkeypatch, [ConnectionRefusedError("private"), True])
    asyncio.run(scope["wait_transport"]())
    attempts = scope["TRANSPORT_ATTEMPTS"]
    assert [a["outcome"] for a in attempts] == ["ConnectionRefusedError", "connected"]
    assert calls[-1] == "closed"
    for a in attempts:
        assert a["host"] == client.HOST and a["port"] == 5432
        assert __import__("datetime").datetime.fromisoformat(a["started_at"]).tzinfo
        assert __import__("datetime").datetime.fromisoformat(a["finished_at"]).tzinfo
    assert "private" not in json.dumps(attempts)


def test_readiness_deadline_is_finite_and_preserves_all_refusals(monkeypatch):
    scope, calls, clock, asyncio = readiness_scope(monkeypatch, [])
    with pytest.raises(TimeoutError):
        asyncio.run(scope["wait_transport"]())
    assert clock[0] == 10.0
    assert len(scope["TRANSPORT_ATTEMPTS"]) == 8
    assert all(a["outcome"] == "ConnectionRefusedError" for a in scope["TRANSPORT_ATTEMPTS"])
    assert len(calls) == 8


@pytest.mark.parametrize("key,value", [("DB_HOST", "foreign.example"), ("DB_PORT", "5433")])
def test_readiness_wrong_target_never_connects(monkeypatch, key, value):
    scope, calls, _, asyncio = readiness_scope(monkeypatch, [True])
    monkeypatch.setenv(key, value)
    with pytest.raises(AssertionError):
        asyncio.run(scope["wait_transport"]())
    assert calls == [] and scope["TRANSPORT_ATTEMPTS"] == []


def test_readiness_unexpected_error_is_not_retried(monkeypatch):
    scope, calls, _, asyncio = readiness_scope(monkeypatch, [PermissionError("hostile-private"), True])
    with pytest.raises(PermissionError):
        asyncio.run(scope["wait_transport"]())
    assert len(calls) == 1
    assert scope["TRANSPORT_ATTEMPTS"][0]["outcome"] == "PermissionError"
    assert "hostile-private" not in json.dumps(scope["TRANSPORT_ATTEMPTS"])
