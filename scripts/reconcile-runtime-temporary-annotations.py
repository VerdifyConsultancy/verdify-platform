#!/usr/bin/env python3
"""Remove only the two obsolete Lab metadata annotations; default is read-only."""

import argparse
import json
import subprocess

NAMES = (
    "operations.vallery.net/temporary-bootstrap-copy",
    "operations.vallery.net/temporary-scale",
)
KUBE = ["kubectl", "--context", "vallery", "-n", "verdify-prod"]


def reconcile(apply=False):
    obj = json.loads(subprocess.check_output(KUBE + ["get", "deployment", "verdify-lab", "-o", "json"]))
    metadata = obj["metadata"]
    assert metadata["namespace"] == "verdify-prod" and metadata["name"] == "verdify-lab"
    keys = [key for key in NAMES if key in metadata.get("annotations", {})]
    if apply and keys:
        patch = [
            {"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
            {"op": "test", "path": "/metadata/resourceVersion", "value": metadata["resourceVersion"]},
        ] + [
            {"op": "remove", "path": "/metadata/annotations/" + key.replace("~", "~0").replace("/", "~1")}
            for key in keys
        ]
        subprocess.run(
            KUBE + ["patch", "deployment", "verdify-lab", "--type=json", "-p", json.dumps(patch)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        after = json.loads(subprocess.check_output(KUBE + ["get", "deployment", "verdify-lab", "-o", "json"]))
        assert after["metadata"]["uid"] == metadata["uid"] and after["spec"] == obj["spec"]
        assert not any(key in after["metadata"].get("annotations", {}) for key in NAMES)
    return {
        "namespace": "verdify-prod",
        "deployment": "verdify-lab",
        "uid": metadata["uid"],
        "obsolete_annotation_keys": keys,
        "applied": bool(apply and keys),
        "pod_spec_changed": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(reconcile(parser.parse_args().apply), indent=2))
