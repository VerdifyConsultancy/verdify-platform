#!/usr/bin/env python3
"""Prepare an exact-source firmware build or reviewed-artifact verification Job.

No resource is created without --submit. Flashing additionally requires the
ordinary attended firmware preflight and explicit authorization in the session.

Two steps of the signed release path are not Jobs. ``inspect`` prepares the
read-only Pod behind the value-blind pre-flash checks. ``pin`` writes an accepted
version to the file alert_monitor reads in the ingestor pod, only while the
controller reports that version, and reads it back. No other supported path
writes that file. docs/runbooks/firmware-signed-artifacts.md has the procedure.
"""

import argparse
import copy
import json
import re
import subprocess

PIN_PATH = "/srv/verdify/state/expected-firmware-version"
PIN_TMP = "/srv/verdify/state/.expected-firmware-version.tmp"
# The same read alert_monitor makes before it compares the pin.
LIVE_VERSION_SQL = (
    "SELECT firmware_version FROM diagnostics WHERE ts >= now() - interval '10 minutes' "
    "AND firmware_version IS NOT NULL ORDER BY ts DESC LIMIT 1"
)


def make_inspect_pod(template: dict, name: str) -> dict:
    """A read-only Pod on the artifacts PVC: no Secret, no token and the deny-all-egress label."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,61}[a-z0-9]", name):
        raise ValueError("explicit DNS-compatible Pod name required")
    template_spec = copy.deepcopy(template["spec"]["jobTemplate"]["spec"]["template"])
    pod = template_spec["spec"]
    tool = next(c for c in pod["containers"] if c["name"] == "flash-reviewed-artifact")
    tool.update(
        name="inspect",
        command=["sleep", "280"],
        args=[],
        env=[e for e in tool["env"] if e["name"] == "HOME"],
        volumeMounts=[m for m in tool["volumeMounts"] if m["name"] in ("release-tools", "tmp")]
        + [{"name": "artifacts", "mountPath": "/artifacts", "readOnly": True}],
    )
    pod.pop("initContainers", None)
    pod.update(
        containers=[tool],
        activeDeadlineSeconds=300,
        terminationGracePeriodSeconds=1,
        volumes=[v for v in pod["volumes"] if v["name"] in ("artifacts", "release-tools", "tmp")],
    )
    labels = template_spec["metadata"]["labels"]
    labels["app.kubernetes.io/component"] = "firmware-artifact-verifier"
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": "verdify-prod", "labels": labels},
        "spec": pod,
    }


def pin_expected_version(kube: list[str], version: str, submit: bool) -> dict:
    """Write the accepted version to the pod's pin. Refuses a version the controller does not report."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", version):
        raise ValueError("invalid firmware version")
    kube_exec = kube + ["-n", "verdify-prod", "exec"]
    psql = ["psql", "-U", "verdify", "-d", "verdify", "-XAtc", LIVE_VERSION_SQL]
    live = subprocess.check_output(kube_exec + ["verdify-db-0", "-c", "postgres", "--"] + psql, text=True).strip()
    if live != version:
        raise ValueError(f"controller reports {live or 'no version'}, not {version}: pin refused")
    ingestor = ["deploy/verdify-ingestor", "-c", "ingestor", "--"]
    if submit:
        # Same directory, so the rename is atomic for the 300 s alert cycle that reads the file.
        subprocess.run(
            kube_exec + ["-i"] + ingestor + ["sh", "-c", f"cat > {PIN_TMP} && mv {PIN_TMP} {PIN_PATH}"],
            input=version + "\n",
            text=True,
            check=True,
        )
        written = subprocess.check_output(kube_exec + ingestor + ["cat", PIN_PATH], text=True).strip()
        if written != version:
            raise ValueError(f"pin reads {written or 'empty'} after the write, not {version}")
    return {
        "pin_path": PIN_PATH,
        "live_firmware_version": live,
        "expected_firmware_version": version,
        "written": submit,
    }


OTA_KEYS = ("ota_password", "ota_password_next", "ota_password_previous")


def make_job(
    template: dict, action: str, revision: str, expected: str, name: str, ota_key: str = "ota_password"
) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("full source revision required")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,61}[a-z0-9]", name):
        raise ValueError("explicit DNS-compatible Job name required")
    if action not in ("build", "verify", "flash"):
        raise ValueError("invalid action")
    if action != "build" and not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("reviewed manifest SHA256 required")
    if ota_key not in OTA_KEYS:
        raise ValueError("invalid OTA key")
    spec = copy.deepcopy(template["spec"]["jobTemplate"]["spec"])
    pod = spec["template"]["spec"]
    for container in pod.get("initContainers", []) + pod["containers"]:
        for env in container.get("env", []):
            if env["name"] == "FW_REVISION":
                env["value"] = revision
            elif env["name"] == "EXPECTED_MANIFEST_SHA256":
                env["value"] = expected
            elif env["name"] == "OTA_PASSWORD":
                # #1009 rotation: build with the next password, flash with the one the device runs.
                env["valueFrom"]["secretKeyRef"]["key"] = ota_key
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
                "verdify.io/ota-secret-key": ota_key if action != "verify" else "",
            },
        },
        "spec": spec,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("build", "verify", "flash", "inspect", "pin"))
    p.add_argument("--revision", default="")
    p.add_argument("--expected", default="")
    p.add_argument("--name", default="")
    p.add_argument("--firmware-version", default="", help="pin: the accepted version, from the release receipt")
    p.add_argument(
        "--ota-key",
        choices=OTA_KEYS,
        default="ota_password",
        help="build/flash: the verdify-firmware-ota key (#1009 rotation: build with ota_password_next)",
    )
    p.add_argument("--submit", action="store_true")
    args = p.parse_args()
    kube = ["kubectl", "--context", "vallery", "--server=https://192.168.30.240:6443", "--request-timeout=15s"]
    if args.action == "pin":
        print(json.dumps(pin_expected_version(kube, args.firmware_version, args.submit)))
        return
    cronjob = "verdify-firmware-builder" if args.action == "build" else "verdify-firmware-flasher"
    template = json.loads(
        subprocess.check_output(kube + ["-n", "verdify-prod", "get", "cronjob", cronjob, "-o", "json"])
    )
    if args.action == "inspect":
        job = make_inspect_pod(template, args.name)
    else:
        job = make_job(template, args.action, args.revision, args.expected, args.name, args.ota_key)
    if args.submit:
        subprocess.run(kube + ["create", "-f", "-"], input=json.dumps(job), text=True, check=True)
    else:
        print(json.dumps(job, indent=2))


if __name__ == "__main__":
    main()
