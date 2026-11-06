"""Artifact substitution and signature failures must stop before device transport.

Compile-time credentials stay exact and out of every report, and the accepted version reaches the pod's pin.
"""

import base64
import hashlib
import importlib.util
import json
import stat
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


def cpp_string(value: bytes) -> bytes:
    """A C literal the way ESPHome's cpp_string_escape writes it: unsafe bytes as three-digit octal."""
    safe = (bytes([b]) if 32 <= b < 127 and b not in b'"\\' else b"\\%03o" % b for b in value)
    return b'"' + b"".join(safe) + b'"'


CREDENTIALS = {
    "WIFI_SSID": 'devices."quoted" net',
    "WIFI_PASSWORD": "pa$$w\\ord: #1 'x' {y} \u00e9",
    "OTA_PASSWORD": "ota-\t-tab-\U0001f331",
}


def generated_main(credentials=CREDENTIALS, **literals) -> bytes:
    lines = [b"#include <esphome.h>"]
    for accessor, setter, name in (
        (b".", "set_ssid", "WIFI_SSID"),
        (b".", "set_password", "WIFI_PASSWORD"),
        (b"->", "set_auth_password", "OTA_PASSWORD"),
    ):
        for value in literals.get(setter, [credentials[name].encode()]):
            lines.append(b"  id" + accessor + setter.encode() + b"(" + cpp_string(value) + b");")
    return b"\n".join(lines) + b"\n"


def image_of(*values: bytes) -> bytes:
    return b"\xe9firmware\0" + b"\0pad\0".join(values) + b"\0tail"


def write_build(tmp_path, main: bytes, image: bytes):
    main_cpp, binary = tmp_path / "main.cpp", tmp_path / "firmware.ota.bin"
    main_cpp.write_bytes(main)
    binary.write_bytes(image)
    return main_cpp, binary


@pytest.mark.parametrize(
    "value",
    [
        'quo"te',
        "back\\slash\\",
        "$fw_version ${x} $$",
        ": # comment",
        "'single' {flow} [seq] !tag &anchor *alias %d",
        " leading and trailing ",
        "new\nline\ttab\r",
        "del\x7f c1\x85\x9f",
        "caf\u00e9 \U0001f331",
    ],
)
def test_secrets_yaml_loads_back_exactly_and_is_private(tmp_path, value):
    path = tmp_path / "secrets.yaml"
    environ = {name: value for name in release.SECRET_KEYS.values()}
    release.write_secrets(path, environ)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert yaml.safe_load(path.read_text()) == dict.fromkeys(release.SECRET_KEYS, value)
    # Never overwrites a file it did not create.
    with pytest.raises(OSError):
        release.write_secrets(path, environ)


def test_secrets_yaml_refuses_an_absent_credential_and_names_only_the_variable(tmp_path, monkeypatch, capsys):
    # An empty OTA password would compile a device with no OTA authentication.
    environ = {name: "s3cret-value" for name in release.SECRET_KEYS.values()}
    environ["OTA_PASSWORD"] = ""
    path = tmp_path / "secrets.yaml"
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("sys.argv", ["firmware_release.py", "secrets-yaml", "--output", str(path)])
    assert release.main() == 64
    assert not path.exists()
    captured = capsys.readouterr()
    assert "OTA_PASSWORD absent" in captured.err and "s3cret-value" not in captured.err + captured.out


def test_c_string_bytes_reads_what_esphome_and_a_compiler_would():
    assert release.c_string_bytes(cpp_string(b'a"b\\c\xc3\xa9\x01')) == b'a"b\\c\xc3\xa9\x01'
    assert release.c_string_bytes(rb'"\n\t\x41\101\?\""') == b'\n\tAA?"'
    with pytest.raises(ValueError, match="unsupported escape"):
        release.c_string_bytes(rb'"\q"')


def test_check_credentials_passes_a_faithful_build_and_reports_no_value(tmp_path):
    encoded = [value.encode() for value in CREDENTIALS.values()]
    main_cpp, binary = write_build(tmp_path, generated_main(), image_of(*encoded))
    report = release.check_credentials(main_cpp, binary, CREDENTIALS)
    assert report == {
        setter: {"literals": 1, "equals_secret": True, "in_binary": 1} for setter in release.CREDENTIAL_SETTERS
    }
    assert not any(value in json.dumps(report) for value in CREDENTIALS.values())


@pytest.mark.parametrize(
    "main,image_values",
    [
        # The shape YAML and substitution mangling leaves: the literal is not the Secret.
        (generated_main(set_password=[b"mangled"]), [b"mangled", b'devices."quoted" net']),
        # A second network or an absent setter is not the one credential this device joins with.
        (generated_main(set_ssid=[b'devices."quoted" net', b"other"]), None),
        (generated_main(set_auth_password=[]), None),
        # The literal is right, the binary does not carry it.
        (generated_main(), [b"unrelated"]),
    ],
)
def test_check_credentials_refuses_before_archive_and_stays_value_blind(tmp_path, main, image_values):
    values = image_values or [value.encode() for value in CREDENTIALS.values()]
    main_cpp, binary = write_build(tmp_path, main, image_of(*values))
    with pytest.raises(ValueError, match="compile-time credential check failed") as refused:
        release.check_credentials(main_cpp, binary, CREDENTIALS)
    for secret in (*CREDENTIALS.values(), "mangled", "other"):
        assert secret not in str(refused.value)


def test_check_credentials_command_reports_and_refuses_with_its_exit_code(tmp_path, monkeypatch, capsys):
    encoded = [value.encode() for value in CREDENTIALS.values()]
    main_cpp, binary = write_build(tmp_path, generated_main(), image_of(*encoded))
    for name, value in CREDENTIALS.items():
        monkeypatch.setenv(name, value)
    argv = ["firmware_release.py", "check-credentials", "--main-cpp", str(main_cpp), "--binary", str(binary)]
    monkeypatch.setattr("sys.argv", argv)
    assert release.main() == 0
    assert json.loads(capsys.readouterr().out)["set_auth_password"]["equals_secret"] is True
    binary.write_bytes(image_of(b"unrelated"))
    assert release.main() == 64
    captured = capsys.readouterr()
    assert "compile-time credential check failed" in captured.err
    assert not any(value in captured.out + captured.err for value in CREDENTIALS.values())


def parity_tree(tmp_path, running_values):
    candidate, running = "c" * 40, "d" * 40
    (tmp_path / candidate / Path(release.GENERATED_MAIN_CPP).parent).mkdir(parents=True)
    (tmp_path / running).mkdir()
    (tmp_path / candidate / release.GENERATED_MAIN_CPP).write_bytes(generated_main())
    (tmp_path / candidate / "firmware.ota.bin").write_bytes(image_of(*(v.encode() for v in CREDENTIALS.values())))
    (tmp_path / running / "firmware.ota.bin").write_bytes(image_of(*running_values))
    return candidate, running


def test_credential_parity_holds_no_secret_and_reports_only_counts(tmp_path, capsys, monkeypatch):
    candidate, running = parity_tree(tmp_path, [v.encode() for v in CREDENTIALS.values()])
    for name in CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
    argv = ["firmware_release.py", "credential-parity", "--root", str(tmp_path)]
    monkeypatch.setattr("sys.argv", [*argv, "--revision", candidate, "--running", running])
    assert release.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {setter: {"literals": 1, "candidate": 1, "running": 1} for setter in release.CREDENTIAL_SETTERS}


def test_credential_parity_stops_when_the_running_image_lacks_a_candidate_credential(tmp_path):
    candidate, running = parity_tree(tmp_path, [b'devices."quoted" net', b"an older password"])
    with pytest.raises(ValueError, match="credential parity failed") as refused:
        release.credential_parity(tmp_path, candidate, running)
    assert '"running": 0' in str(refused.value)
    assert not any(value in str(refused.value) for value in CREDENTIALS.values())


def test_ota_rotation_parity_requires_a_new_ota_literal_and_unchanged_wifi(tmp_path):
    """#1009: a rotation candidate carries the next OTA password; Wi-Fi must still match."""
    ssid, wifi = (CREDENTIALS[k].encode() for k in ("WIFI_SSID", "WIFI_PASSWORD"))
    candidate, running = parity_tree(tmp_path, [ssid, wifi, b"the old ota password"])
    with pytest.raises(ValueError, match="credential parity failed"):
        release.credential_parity(tmp_path, candidate, running)
    report = release.credential_parity(tmp_path, candidate, running, rotate_ota=True)
    assert report["set_auth_password"] == {"literals": 1, "candidate": 1, "running": 0}
    assert report["ota_rotation"] is True
    assert not any(value in json.dumps(report) for value in CREDENTIALS.values())
    # The same candidate as the running image is not a rotation.
    tmp_same = tmp_path / "same"
    tmp_same.mkdir()
    candidate, running = parity_tree(tmp_same, [v.encode() for v in CREDENTIALS.values()])
    with pytest.raises(ValueError, match="credential parity failed"):
        release.credential_parity(tmp_same, candidate, running, rotate_ota=True)
    # A Wi-Fi change is never covered by a rotation.
    tmp_wifi = tmp_path / "wifi"
    tmp_wifi.mkdir()
    candidate, running = parity_tree(tmp_wifi, [ssid, b"another wifi", b"the old ota password"])
    with pytest.raises(ValueError, match="credential parity failed"):
        release.credential_parity(tmp_wifi, candidate, running, rotate_ota=True)


@pytest.mark.parametrize("action,component", [("build", "firmware-builder.yaml"), ("flash", "firmware-flasher.yaml")])
def test_ota_key_selects_the_rotation_key_and_refuses_others(action, component):
    """#1009: build with ota_password_next, flash with the key the device runs; nothing else."""
    docs = list(yaml.safe_load_all((COMPONENT / component).read_text()))
    template = next(d for d in docs if d["kind"] == "CronJob")
    expected = "" if action == "build" else "b" * 64

    def ota_keys(job):
        pod = job["spec"]["template"]["spec"]
        return {
            e["valueFrom"]["secretKeyRef"]["key"]
            for c in pod.get("initContainers", []) + pod["containers"]
            for e in c.get("env", [])
            if e["name"] == "OTA_PASSWORD"
        }

    assert ota_keys(jobs.make_job(template, action, SHA, expected, "fixture")) == {"ota_password"}
    job = jobs.make_job(template, action, SHA, expected, "fixture", "ota_password_next")
    assert ota_keys(job) == {"ota_password_next"}
    assert job["metadata"]["annotations"]["verdify.io/ota-secret-key"] == "ota_password_next"
    assert ota_keys(jobs.make_job(template, action, SHA, expected, "fixture")) == {"ota_password"}
    with pytest.raises(ValueError, match="invalid OTA key"):
        jobs.make_job(template, action, SHA, expected, "fixture", "wifi_password")


def test_verify_receipt_carries_the_elf_hash_the_c1_pin_needs(signed, tmp_path, capsys):
    directory, expected, public = signed
    staged = tmp_path / "staged"
    staged.mkdir()
    release.stage(tmp_path, SHA, expected, staged)
    release.finish(staged, SHA, expected, public, flash=False)
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["elf_sha256"] == release.sha256(directory / "firmware.elf")


def test_inspect_pod_is_read_only_and_can_reach_nothing():
    docs = list(yaml.safe_load_all((COMPONENT / "firmware-flasher.yaml").read_text()))
    template = next(d for d in docs if d["kind"] == "CronJob")
    original = json.dumps(template, sort_keys=True)
    pod = jobs.make_inspect_pod(template, "firmware-inspect-fixture")
    assert json.dumps(template, sort_keys=True) == original
    assert pod["kind"] == "Pod" and pod["metadata"]["namespace"] == "verdify-prod"
    assert pod["metadata"]["labels"]["app.kubernetes.io/component"] == "firmware-artifact-verifier"
    spec = pod["spec"]
    assert spec["automountServiceAccountToken"] is False and spec["activeDeadlineSeconds"] == 300
    assert "initContainers" not in spec and [c["name"] for c in spec["containers"]] == ["inspect"]
    container = spec["containers"][0]
    assert not any("valueFrom" in e for e in container["env"])
    assert {"name": "artifacts", "mountPath": "/artifacts", "readOnly": True} in container["volumeMounts"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    claims = [v["persistentVolumeClaim"] for v in spec["volumes"] if "persistentVolumeClaim" in v]
    assert claims == [{"claimName": "verdify-firmware-artifacts", "readOnly": True}]
    assert not any("secret" in v for v in spec["volumes"])
    with pytest.raises(ValueError):
        jobs.make_inspect_pod(template, "")


class FakeCluster:
    """Answers the helper's kubectl calls; records every call without a cluster."""

    def __init__(self, live, pin=""):
        self.live, self.pin, self.calls, self.writes = live, pin, [], []

    def check_output(self, argv, text=None):
        self.calls.append(argv)
        return (self.live if "psql" in argv else self.pin) + "\n"

    def run(self, argv, input=None, text=None, check=None):  # noqa: A002 - subprocess's own keyword
        self.calls.append(argv)
        self.writes.append(argv)
        self.pin = input.strip()


def test_pin_writes_atomically_and_reads_back(monkeypatch):
    cluster = FakeCluster("2026.11.3.0435.5e24d07c")
    monkeypatch.setattr(jobs, "subprocess", cluster)
    receipt = jobs.pin_expected_version(["kubectl"], "2026.11.3.0435.5e24d07c", submit=True)
    assert receipt["written"] is True and cluster.pin == "2026.11.3.0435.5e24d07c"
    write = cluster.writes[0]
    assert write[write.index("--") - 3 : write.index("--")] == ["deploy/verdify-ingestor", "-c", "ingestor"]
    assert write[-1] == (
        "cat > /srv/verdify/state/.expected-firmware-version.tmp "
        "&& mv /srv/verdify/state/.expected-firmware-version.tmp /srv/verdify/state/expected-firmware-version"
    )
    assert cluster.calls[-1][-2:] == ["cat", "/srv/verdify/state/expected-firmware-version"]


def test_pin_without_submit_writes_nothing(monkeypatch):
    cluster = FakeCluster("2026.11.3.0435.5e24d07c")
    monkeypatch.setattr(jobs, "subprocess", cluster)
    assert jobs.pin_expected_version(["kubectl"], "2026.11.3.0435.5e24d07c", submit=False)["written"] is False
    assert cluster.writes == []


@pytest.mark.parametrize("live", ["2026.10.2.0637.620a218a-wifi-bound", ""])
def test_pin_refuses_a_version_the_controller_does_not_report(monkeypatch, live):
    cluster = FakeCluster(live)
    monkeypatch.setattr(jobs, "subprocess", cluster)
    with pytest.raises(ValueError, match="pin refused"):
        jobs.pin_expected_version(["kubectl"], "2026.11.3.0435.5e24d07c", submit=True)
    assert cluster.writes == []


@pytest.mark.parametrize("version", ["", "a b", "x;rm -rf /", "-flag", "a" * 129])
def test_pin_refuses_a_malformed_version_before_any_call(monkeypatch, version):
    cluster = FakeCluster(version)
    monkeypatch.setattr(jobs, "subprocess", cluster)
    with pytest.raises(ValueError, match="invalid firmware version"):
        jobs.pin_expected_version(["kubectl"], version, submit=True)
    assert cluster.calls == []
