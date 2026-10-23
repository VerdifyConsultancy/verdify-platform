"""Artifact substitution and signature failures must stop before device transport."""

import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/firmware-builder"
SPEC = importlib.util.spec_from_file_location("firmware_release", COMPONENT / "firmware_release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
JOB_SPEC = importlib.util.spec_from_file_location("firmware_job", ROOT / "scripts/firmware-release-job.py")
jobs = importlib.util.module_from_spec(JOB_SPEC)
JOB_SPEC.loader.exec_module(jobs)
SHA = "a" * 40


@pytest.fixture
def signed(tmp_path):
    directory = tmp_path / SHA
    directory.mkdir()
    for name in release.FILES:
        (directory / name).write_bytes(b"nondeployable test fixture " + name.encode())
    release.write_manifest(tmp_path, SHA, "test-only", "example.test/compiler@sha256:" + "b" * 64)
    payload = (directory / "release.json").read_bytes()
    key = ec.generate_private_key(ec.SECP256R1())
    public = tmp_path / "test.pub"
    public.write_bytes(
        key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    bundle = {
        "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
        "messageSignature": {
            "messageDigest": {
                "algorithm": "SHA2_256",
                "digest": base64.b64encode(hashlib.sha256(payload).digest()).decode(),
            },
            "signature": base64.b64encode(key.sign(payload, ec.ECDSA(hashes.SHA256()))).decode(),
        },
    }
    (directory / "release.sigstore.json").write_text(json.dumps(bundle))
    return directory, hashlib.sha256(payload).hexdigest(), public


def test_signed_private_copy_verifies_without_loading_device_transport(signed, tmp_path, capsys):
    directory, expected, public = signed
    target = tmp_path / "private"
    target.mkdir()
    release.stage(tmp_path, SHA, expected, target)
    # Mutation after staging cannot change bytes used by the verified upload.
    (directory / "firmware.ota.bin").write_bytes(b"tampered after copy")
    release.finish(target, SHA, expected, public, False)
    assert json.loads(capsys.readouterr().out)["device_contact"] is False


@pytest.mark.parametrize("change", ["binary", "manifest", "signature", "key", "source", "symlink"])
def test_tampering_and_wrong_identity_refuse_before_ota(signed, tmp_path, change):
    directory, expected, public = signed
    revision = SHA
    if change == "binary":
        (directory / "firmware.ota.bin").write_bytes(b"tamper")
    elif change == "manifest":
        (directory / "release.json").write_text("{}")
    elif change == "signature":
        bundle = json.loads((directory / "release.sigstore.json").read_text())
        bundle["messageSignature"]["signature"] = base64.b64encode(b"not a signature").decode()
        (directory / "release.sigstore.json").write_text(json.dumps(bundle))
    elif change == "key":
        key = ec.generate_private_key(ec.SECP256R1())
        public.write_bytes(
            key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        )
    elif change == "source":
        revision = "c" * 40
    else:
        binary = directory / "firmware.ota.bin"
        binary.rename(tmp_path / "other")
        binary.symlink_to(tmp_path / "other")
    with pytest.raises(ValueError):
        release.finish(directory, revision, expected, public, True)


def test_release_is_immutable(signed):
    directory, _, _ = signed
    with pytest.raises(FileExistsError):
        release.write_manifest(directory.parent, SHA, "test-only", "example.test/compiler@sha256:" + "b" * 64)


@pytest.mark.parametrize("revision", ["main", "../escape", "A" * 40, "a" * 39])
def test_release_path_requires_full_source_sha(tmp_path, revision):
    with pytest.raises(ValueError):
        release.release_directory(tmp_path, revision)


def test_flashing_is_separate_and_has_no_build_or_signing_credentials():
    docs = list(yaml.safe_load_all((COMPONENT / "firmware-flasher.yaml").read_text()))
    job = next(d for d in docs if d["kind"] == "CronJob")
    assert job["spec"]["suspend"]
    pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert [c["name"] for c in pod["initContainers"]] == ["stage-reviewed-artifact", "verify-signature"]
    assert pod["containers"][0]["args"] == ["verify"]
    refs = {
        e["valueFrom"]["secretKeyRef"]["name"]
        for c in pod["initContainers"] + pod["containers"]
        for e in c.get("env", [])
        if "valueFrom" in e
    }
    assert refs == {"verdify-firmware-ota"}
    assert all(v["name"] != "signing" for v in pod["volumes"])
    policy = next(d for d in docs if d["kind"] == "NetworkPolicy")
    assert policy["spec"]["egress"] == [
        {"to": [{"ipBlock": {"cidr": "192.168.10.111/32"}}], "ports": [{"port": 3232, "protocol": "TCP"}]}
    ]


def test_verify_job_has_no_ota_credential_and_is_selected_by_deny_egress():
    docs = list(yaml.safe_load_all((COMPONENT / "firmware-flasher.yaml").read_text()))
    template = next(d for d in docs if d["kind"] == "CronJob")
    job = jobs.make_job(template, "verify", SHA, "b" * 64, "firmware-verify-fixture")
    pod = job["spec"]["template"]
    assert pod["metadata"]["labels"]["app.kubernetes.io/component"] == "firmware-artifact-verifier"
    for c in pod["spec"]["initContainers"] + pod["spec"]["containers"]:
        assert not any("secretKeyRef" in e.get("valueFrom", {}) for e in c.get("env", []))
    policy = next(d for d in docs if d["metadata"]["name"] == "firmware-verifier-deny-egress")
    assert policy["spec"]["podSelector"]["matchLabels"] == {"app.kubernetes.io/component": "firmware-artifact-verifier"}
    assert policy["spec"]["egress"] == []


def test_build_job_binds_compiler_and_signer_to_same_reviewed_revision():
    docs = list(yaml.safe_load_all((COMPONENT / "firmware-builder.yaml").read_text()))
    template = next(d for d in docs if d["kind"] == "CronJob")
    job = jobs.make_job(template, "build", SHA, "", "firmware-build-fixture")
    pod = job["spec"]["template"]["spec"]
    for c in pod["initContainers"] + pod["containers"]:
        assert next(e["value"] for e in c["env"] if e["name"] == "FW_REVISION") == SHA
    assert not any(v["name"] == "signing" for v in pod["initContainers"][0]["volumeMounts"])


@pytest.mark.parametrize("revision,expected", [("main", "b" * 64), (SHA, ""), (SHA, "b" * 63)])
def test_job_rejects_mutable_source_or_missing_review_digest(revision, expected):
    with pytest.raises(ValueError):
        jobs.make_job({}, "verify", revision, expected, "firmware-refusal-fixture")
