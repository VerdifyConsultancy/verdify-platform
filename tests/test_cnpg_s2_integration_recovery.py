"""Fresh recovery cannot inherit the background-job drift or broaden admission."""

import copy
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CLUSTERS = ROOT / "deploy/k8s/cnpg/rehearsal/cluster"


def load(filename, module_name):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "scripts" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


m = load("cnpg-s2-integration-current275-transition.py", "integration_current275")


def test_fresh_clone_keeps_exact_accepted_reader_lineage_and_stops_jobs_before_start():
    before = list(yaml.safe_load_all((CLUSTERS / "s2-current274-frozen-recovery-pair.yaml").read_text()))[1]
    after = yaml.safe_load((CLUSTERS / "s2-current274-integration-recovery.yaml").read_text())
    assert after["metadata"]["name"] == m.CLUSTER
    assert after["spec"]["postgresql"]["parameters"]["timescaledb.max_background_workers"] == "0"
    assert "plugins" not in after["spec"]
    assert after["spec"]["enableSuperuserAccess"] is False
    assert after["spec"]["bootstrap"]["recovery"]["recoveryTarget"] == {
        "backupID": "20261006T052358",
        "targetTime": "2026-10-06T05:38:19.219172+00:00",
        "targetTLI": "1",
        "exclusive": False,
    }
    normalized = copy.deepcopy(after)
    normalized["metadata"]["name"] = before["metadata"]["name"]
    del normalized["metadata"]["annotations"]["verdify.ai/recovery-purpose"]
    assert normalized == before


@pytest.mark.parametrize("target", ["verdify-db", "verdify-cnpg-s2", "verdify-cnpg-s2-pitr-b-frozen", "arbitrary"])
def test_integration_adapter_refuses_every_other_target(target):
    with pytest.raises(ValueError, match="closed275"):
        m.transition.configured(target)


def test_new_admission_retains_historical_source_and_custody_guards():
    old = load("cnpg-current275-transition.py", "historical_current275")
    targets = old.TARGETS
    m.transition.configured(m.CLUSTER)
    ddl, bodies = m.transition.t.ddl(True, physical_target=m.CLUSTER)
    assert "current_setting('cluster_name')<>'" + m.CLUSTER + "'" in ddl
    assert len(bodies) == 2
    assert old.TARGETS == targets == ("verdify-cnpg-s2", "verdify-cnpg-s2-pitr-b-frozen")
    assert old.VERSION == "cnpg-current275-additive-transition-v1"
    for key in (
        "NATIVE274_WITNESS_SHA",
        "NATIVE275_BRIDGE_WITNESS_SHA",
        "MIGRATION_SHA",
        "CANONICAL_BEFORE",
        "CANONICAL_AFTER",
    ):
        assert getattr(m.transition, key) == getattr(old, key)
    raw = (ROOT / old.MIGRATION).read_bytes()
    assert m.transition.remap_migration(raw) == old.remap_migration(raw)


def test_client_network_can_only_reach_the_new_target_and_dns():
    policies = list(yaml.safe_load_all((CLUSTERS / "cnpg-s2-integration-runtime-client-policy.yaml").read_text()))
    egress, ingress = policies
    assert egress["spec"]["podSelector"]["matchLabels"]["verdify.ai/qualification-target"] == m.CLUSTER
    assert egress["spec"]["egress"][0] == {
        "to": [{"podSelector": {"matchLabels": {"cnpg.io/cluster": m.CLUSTER}}}],
        "ports": [{"protocol": "TCP", "port": 5432}],
    }
    assert len(egress["spec"]["egress"]) == 2
    assert egress["spec"]["egress"][1]["ports"] == [{"protocol": "TCP", "port": 53}, {"protocol": "UDP", "port": 53}]
    assert "ingress" not in egress["spec"]
    assert ingress["spec"]["podSelector"]["matchLabels"] == {"cnpg.io/cluster": m.CLUSTER}
    assert ingress["spec"]["ingress"][0]["from"] == [{"podSelector": egress["spec"]["podSelector"]}]
