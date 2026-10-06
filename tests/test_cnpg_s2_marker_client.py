"""Refusal tests; fabricated markers never count as live recovery evidence."""

import copy
import importlib.util
from pathlib import Path

import pytest
from test_cnpg_s2_pitr_pair import s2_fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("s2_markers", ROOT / "scripts/render-cnpg-s2-marker-client.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture():
    cluster, backup, _, admission = s2_fixture()
    cluster["status"] = {"currentPrimary": "verdify-cnpg-s2-1", "readyInstances": 3}
    primary = {
        "metadata": {
            "name": "verdify-cnpg-s2-1",
            "namespace": "verdify-db-rehearsal",
            "uid": "6379db06-2bad-4c5b-9a50-86feac4651dc",
            "labels": {"cnpg.io/cluster": "verdify-cnpg-s2"},
        },
        "status": {"phase": "Running", "podIP": "10.42.6.1"},
    }
    admission["binding"]["pod_uid"] = primary["metadata"]["uid"]
    return cluster, primary, backup, admission, "s2-fixture-unique"


def test_only_fixed_bootstrap_tcp_and_existing_secret_refs_with_separate_statement_roundtrips():
    job = m.render(*fixture())
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    container = pod["containers"][0]
    env = {v["name"]: v for v in container["env"]}
    assert env["PGHOST"]["value"] == "verdify-cnpg-s2-rw.verdify-db-rehearsal.svc.cluster.local"
    assert env["PGDATABASE"]["value"] == env["PGUSER"]["value"] == "rehearsal_bootstrap"
    assert env["PGPASSWORD"]["valueFrom"]["secretKeyRef"] == {"name": "verdify-cnpg-s2-app", "key": "password"}
    assert container["command"][:2] == ["sh", "-c"]
    assert "| psql -X -qAt -v ON_ERROR_STOP=1" in container["command"][2]
    assert "until pg_isready" in container["command"][2]
    assert '"$i" -lt 20' in container["command"][2]
    assert container["command"][2].count("| psql ") == 1
    sql = container["command"][-1]
    assert sql.count("BEGIN;\n") == sql.count("COMMIT;\n") == 3
    assert sql.count("'kind','boundary'") == 2
    assert "'xid',xmin::text" in sql and "acknowledged_flush_lsn" in sql
    assert "synchronous_commit" in sql and "inet_client_addr() IS NULL" in sql
    assert "'server_address',pg_catalog.host(inet_server_addr())" in sql
    assert sql.index("pg_walfile_name(pg_current_wal_flush_lsn()) IS NULL") < sql.index("INSERT INTO")
    assert "verdify_rehearsal" not in sql and "DROP " not in sql


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a: a[3].update(full_data_internal_catalog_accounting_complete=False),
        lambda a: a[2]["status"].update(phase="running"),
        lambda a: a[0]["metadata"].update(uid="12345678-1234-4234-8234-123456789abc"),
        lambda a: a[1]["status"].update(podIP="not-an-address"),
        lambda a: a.__setitem__(4, "s2-injected'; DROP DATABASE verdify_rehearsal;--"),
    ],
)
def test_refuses_unsealed_or_injected_client(mutate):
    args = list(copy.deepcopy(fixture()))
    mutate(args)
    with pytest.raises(ValueError):
        m.render(*args)
