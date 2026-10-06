"""Render only the two fresh S2 recovery Clusters from sealed native custody.

Uses the unchanged strict A/B/C WAL lineage validator on the fixed S2 source.
The existing reader ObjectStore is retained; no archive writer is rendered.
"""

import argparse
import importlib.util
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("s2_pitr_pair", ROOT / "scripts/render-cnpg-pitr-pair.py")
pitr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pitr)
SOURCE = "verdify-cnpg-s2"
SOURCE_UID = "4f697776-df25-4e22-b930-0b76cf35496e"
INSTALL_SHA = "9abb6d1e4296614ab2cd617a3d0e65d0aa8a39618b5f47ace5f8fa00224439fa"
pitr.SOURCE, pitr.SOURCE_UID = SOURCE, SOURCE_UID


def render(cluster, backup, custody, admission):
    pitr.require(
        admission.get("schema") == "cnpg-s2-native-admission-v1"
        and admission.get("cluster") == SOURCE
        and admission.get("binding", {}).get("cluster_uid") == SOURCE_UID
        and admission.get("database_oid") == 16447
        and admission.get("installation_sha256") == INSTALL_SHA
        and admission.get("full_data_internal_catalog_accounting_complete") is True
        and admission.get("historical_seals_and_ledger_retained") is True,
        "S2 baseline lacks complete native admission/data/time seal",
    )
    declared = yaml.safe_load((ROOT / "deploy/k8s/cnpg/rehearsal/cluster/s2-cluster.yaml").read_text())
    for field in ("storage", "walStorage", "affinity", "resources", "instances", "imageName"):
        actual = cluster["spec"].get(field)
        if field in ("storage", "walStorage") and isinstance(actual, dict):
            actual = dict(actual)
            pitr.require(actual.pop("resizeInUseVolumes", True) is True, "S2 native resize default drift")
        pitr.require(actual == declared["spec"][field], "S2 declared capacity/isolation drift")
    objects = pitr.render(cluster, backup, custody)
    clusters = objects[1:]
    pitr.require(len(clusters) == 2 and objects[0]["metadata"]["name"] == pitr.STORE, "reader lineage drift")
    for suffix, restored in zip(("a", "b"), clusters, strict=True):
        restored["metadata"]["name"] = "verdify-cnpg-s2-pitr-" + suffix
        for field in ("storage", "walStorage", "affinity", "resources", "instances"):
            restored["spec"][field] = declared["spec"][field]
    return clusters


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cluster", "backup", "custody", "admission", "capture-directory", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    custody = json.loads(args.custody.read_text())
    pitr.validate_captures(custody, args.capture_directory)
    objects = render(
        json.loads(args.cluster.read_text()),
        json.loads(args.backup.read_text()),
        custody,
        json.loads(args.admission.read_text()),
    )
    with args.output.open("x") as stream:
        yaml.safe_dump_all(objects, stream, sort_keys=False)


if __name__ == "__main__":
    main()
