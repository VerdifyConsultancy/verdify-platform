"""Independent-reader failure paths cannot emit complete protected-WAL proof."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from test_cnpg_s2_pitr_pair import s2_fixture

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("s2_reader", ROOT / "scripts/render-cnpg-s2-archive-reader.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture():
    args = s2_fixture()
    for i, name in enumerate(("A", "B", "C")):
        args[2]["markers"][name]["acknowledged_flush_lsn"] = f"0/{0x3000100 + i:X}"
    return args


def test_reader_is_bound_to_backup_wal_through_c_without_database_or_writer_identity():
    job = m.render(*fixture())
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    container = pod["containers"][0]
    text = str(job)
    assert "s3-writer" not in text and "PGPASSWORD" not in text and "-app" not in text
    env = {x["name"]: x for x in container["env"]}
    bound = json.loads(env["EXPECTED_ARCHIVE"]["value"])
    assert bound["wal_names"] == [f"00000001000000000000000{i}" for i in (1, 2, 3)]
    assert all(
        env[x]["valueFrom"]["secretKeyRef"]["name"] == "verdify-cnpg-rehearsal-s3-reader"
        for x in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    )
    assert "get_object" in m.READER and "head_object" in m.READER
    assert "put_object" not in m.READER and "delete_object" not in m.READER


@pytest.mark.parametrize("lsn", ["0/1", "9/0"])
def test_refuses_reversed_or_unbounded_wal_protection_range(lsn):
    args = fixture()
    args[2]["markers"]["C"]["acknowledged_flush_lsn"] = lsn
    with pytest.raises(ValueError):
        m.render(*args)


def test_archive_reader_policy_has_no_ingress_or_database_route():
    policy = yaml.safe_load((ROOT / "deploy/k8s/cnpg/rehearsal/cluster/cnpg-s2-archive-reader-policy.yaml").read_text())
    spec = policy["spec"]
    assert "ingress" not in spec
    assert spec["podSelector"]["matchLabels"] == m.render(*fixture())["spec"]["template"]["metadata"]["labels"]
    assert {port["port"] for rule in spec["egress"] for port in rule["ports"]} == {53, 443, 8443}
    assert all(
        "ipBlock" not in target or target["ipBlock"]["cidr"] == "192.168.7.10/32"
        for rule in spec["egress"]
        for target in rule["to"]
    )
