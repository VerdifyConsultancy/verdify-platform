#!/usr/bin/env python3
"""Prepare an exact-source firmware build or reviewed-artifact verification Job.

No resource is created without --submit. Flashing additionally requires the
ordinary attended firmware preflight and explicit authorization in the session.
"""

import argparse
import copy
import json
import re
import subprocess


def make_job(template: dict, action: str, revision: str, expected: str, name: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("full source revision required")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,61}[a-z0-9]", name):
        raise ValueError("explicit DNS-compatible Job name required")
    if action not in ("build", "verify", "flash"):
        raise ValueError("invalid action")
    if action != "build" and not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("reviewed manifest SHA256 required")
    spec = copy.deepcopy(template["spec"]["jobTemplate"]["spec"])
    pod = spec["template"]["spec"]
    for container in pod.get("initContainers", []) + pod["containers"]:
        for env in container.get("env", []):
            if env["name"] == "FW_REVISION":
                env["value"] = revision
            elif env["name"] == "EXPECTED_MANIFEST_SHA256":
                env["value"] = expected
    if action != "build":
        uploader = next(c for c in pod["containers"] if c["name"] == "flash-reviewed-artifact")
        uploader["args"] = [action]
        if action == "verify":
            spec["template"]["metadata"]["labels"]["app.kubernetes.io/component"] = "firmware-artifact-verifier"
            uploader["env"] = [e for e in uploader["env"] if e["name"] != "OTA_PASSWORD"]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": "verdify-prod",
            "annotations": {
                "verdify.io/firmware-source": revision,
                "verdify.io/firmware-action": action,
                "verdify.io/reviewed-manifest-sha256": expected,
            },
        },
        "spec": spec,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("build", "verify", "flash"))
    p.add_argument("--revision", required=True)
    p.add_argument("--expected", default="")
    p.add_argument("--name", required=True)
    p.add_argument("--submit", action="store_true")
    args = p.parse_args()
    kube = ["kubectl", "--context", "vallery", "--server=https://192.168.30.240:6443", "--request-timeout=15s"]
    cronjob = "verdify-firmware-builder" if args.action == "build" else "verdify-firmware-flasher"
    template = json.loads(
        subprocess.check_output(kube + ["-n", "verdify-prod", "get", "cronjob", cronjob, "-o", "json"])
    )
    job = make_job(template, args.action, args.revision, args.expected, args.name)
    if args.submit:
        subprocess.run(kube + ["create", "-f", "-"], input=json.dumps(job), text=True, check=True)
    else:
        print(json.dumps(job, indent=2))


if __name__ == "__main__":
    main()
