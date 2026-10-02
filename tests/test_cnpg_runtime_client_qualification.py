"""Authenticated target-client rendering: fail closed outside the rehearsal."""

import copy
import hashlib
import importlib.util
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
    assert capsys.readouterr().out == '{"status": "failed", "error_category": "KeyError"}\n'


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
        "readonly": ("transaction_read_only", "off"),
    }
    if bad in changes:
        key, value = changes[bad]
        identity[key] = value

    class Context:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, *_args):
            return None

    class Connection:
        identity_reads = 0

        async def fetchrow(self, _sql):
            self.identity_reads += 1
            if bad == "readonly_reset" and self.identity_reads == 2:
                return {**identity, "default_read_only": "off"}
            return identity

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
        "primary_address": "10.42.3.7",
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
        "DB_NAME": client.DATABASE,
        "VERDIFY_DEVICE_WRITE_ENABLED": "0",
    }.items():
        monkeypatch.setenv(key, value)
    if bad is None:
        exec(client.WRAPPED_PROBE, {})  # noqa: S102 — fixed probe under isolated driver double
    else:
        with pytest.raises(SystemExit) as exit_info:
            exec(client.WRAPPED_PROBE, {})  # noqa: S102 — fixed probe under isolated driver double
        assert exit_info.value.code == 1
    output = capsys.readouterr().out
    assert secret not in output and "postgresql://" not in output
    result = json.loads(output)
    assert result["status"] == ("passed" if bad is None else "failed")
    assert "password_tcp_pool_startup" in calls
    if bad != "password":
        assert "actual_consumer_init_callback" in calls and "pool_closed" in calls
    if bad is None:
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
