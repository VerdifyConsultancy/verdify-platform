"""Unit tests for the GHA firmware-secretless-compile program (#1397 step 5).

Moved from jvallery/agents (test_render_ci_submission_admission.py), where they
exercised the inline program of the native registry step. That step is retired
and ci/gha/firmware_secretless_compile.py is now the only copy, so the tests
load it from here. The compiler is faked; simulated compiles are never actual
compile credit.
"""

import contextlib
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REAL_RUN = subprocess.run

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / "ci/gha/firmware_secretless_compile.py"
_SPEC = importlib.util.spec_from_file_location("firmware_secretless_compile_under_test", PROGRAM)
MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(MODULE)


class FirmwareProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.old = Path.cwd()
        os.chdir(self.root)
        subprocess.run(["git", "init", "-q"], check=True)
        f = self.root / "firmware"
        f.mkdir()
        (f / "greenhouse.yaml").write_text("api:\n  services:\n    - service: example\n      then: []\n")
        (f / "greenhouse-recovery.yaml").write_text("packages:\n  base: !include greenhouse.yaml\n")
        (f / "build_api_batch_patch.py").write_text("VERSION='2026.6.5'\n")
        self.commit()
        self.source = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        self.commands = []

    def tearDown(self):
        os.chdir(self.old)
        self.tmp.cleanup()

    def commit(self):
        subprocess.run(["git", "add", "firmware"], check=True)
        subprocess.run(
            ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"],
            check=True,
        )

    def fake_compile(self, cmd, **kw):
        self.commands.append((cmd, kw))
        self.assertEqual(
            set(kw["env"]),
            {
                "PATH",
                "HOME",
                "TMPDIR",
                "PLATFORMIO_CORE_DIR",
                "ESPHOME_DEFAULT_COMPILE_PROCESS_LIMIT",
                "SOURCE_DATE_EPOCH",
            },
        )
        self.assertIn(cmd[-2], ("config", "compile"))
        config = Path(cmd[-1])
        self.assertNotEqual(config.parent, self.root / "firmware")
        self.assertEqual((config.parent / "secrets.yaml").read_text(), MODULE.DUMMY)
        if cmd[-2] == "compile":
            if "CI_NONDEPLOYABLE_GENERATED_SERVICE_MUST_NOT_EXIST" in config.read_text():
                return subprocess.CompletedProcess(
                    cmd, 1, b"error: CI_NONDEPLOYABLE_GENERATED_SERVICE_MUST_NOT_EXIST not declared"
                )
            out = config.parent / ".esphome/build/greenhouse"
            (out / ".pioenvs/greenhouse").mkdir(parents=True, exist_ok=True)
            (out / "src").mkdir(exist_ok=True)
            (out / ".pioenvs/greenhouse/firmware.ota.bin").write_bytes(b"NONDEPLOYABLE fixture binary")
            (out / "src/main.cpp").write_text("fixture generated C++")
            (out / "platformio.ini").write_text("fixture toolchain config")
            pkg = Path(kw["env"]["PLATFORMIO_CORE_DIR"]) / "packages/compiler"
            pkg.mkdir(parents=True, exist_ok=True)
            (pkg / "package.json").write_text('{"name":"compiler_fixture","version":"1.0"}')
            (pkg / "bin").mkdir(exist_ok=True)
            for prefix in ("xtensa-esp-elf", "xtensa-esp32-elf", "xtensa-esp32s2-elf", "xtensa-esp32s3-elf"):
                for suffix in ("as", "g++", "gcc", "ld"):
                    (pkg / "bin" / f"{prefix}-{suffix}").write_bytes(b"NONDEPLOYABLE compiler fixture")
        return subprocess.CompletedProcess(cmd, 0)

    def invoke(self, runner=None, version="2026.6.5", bound=None):
        env = {
            "FLEET_CI_HEAD_REVISION": bound or self.source,
            "OTA_PASSWORD": "not-forwarded",
            "KUBECONFIG": "/not-forwarded",
        }
        out = io.StringIO()
        selected = runner or self.fake_compile

        def dispatch(cmd, **kw):
            # platform helpers shell out to uname/file on macOS; only ESPHome is faked.
            return REAL_RUN(cmd, **kw) if cmd[0] in ("git", "uname", "file") else selected(cmd, **kw)

        with (
            patch.dict(os.environ, env),
            patch.object(importlib.metadata, "version", return_value=version),
            patch.object(subprocess, "run", side_effect=dispatch),
            contextlib.redirect_stdout(out),
        ):
            MODULE.main()
        return json.loads(out.getvalue().split("FIRMWARE_SECRETLESS_RECEIPT=")[1])

    def test_both_targets_compile_and_real_generated_cpp_negative_is_required(self):
        # An ignored operator secret and symlink are not read/exported.
        (self.root / "firmware/secrets.yaml").symlink_to("/definitely-not-readable-live-secret")
        receipt = self.invoke()
        self.assertEqual([x["target"] for x in receipt["targets"]], list(MODULE.TARGETS))
        self.assertFalse(receipt["deployable"])
        self.assertFalse(receipt["physical_qualification"])
        self.assertEqual(receipt["generated_cpp_negative"]["result"], "expected_compile_failure")
        self.assertEqual(sum(c[-2] == "compile" for c, _ in self.commands), 3)
        self.assertEqual(
            (self.root / "firmware/greenhouse.yaml").read_text(),
            "api:\n  services:\n    - service: example\n      then: []\n",
        )
        packages = receipt["compiler_provenance"]["packages"]
        self.assertEqual(receipt["platformio_packages"], {p["name"]: p["version"] for p in packages})
        self.assertEqual(sum(len(p["compiler_binaries"]) for p in packages), 16)
        self.assertFalse(Path(self.commands[-1][1]["env"]["PLATFORMIO_CORE_DIR"]).exists())

    def test_normal_compiler_failure_is_not_a_pass(self):
        def failing(cmd, **kw):
            if cmd[-2] == "compile":
                raise subprocess.CalledProcessError(1, cmd)
            return self.fake_compile(cmd, **kw)

        with self.assertRaises(subprocess.CalledProcessError):
            self.invoke(failing)

    def test_wrong_toolchain_refuses_before_compilation(self):
        with self.assertRaisesRegex(RuntimeError, "wrong ESPHome"):
            self.invoke(version="2026.6.2")
        self.assertFalse(self.commands)

    def test_wrong_native_source_refuses(self):
        with self.assertRaisesRegex(RuntimeError, "revision differs"):
            self.invoke(bound="0" * 40)
        self.assertFalse(self.commands)

    def test_tracked_secret_or_symlink_refuses_without_reading_target(self):
        (self.root / "firmware/secrets.yaml").write_text("dummy forbidden tracked config")
        self.commit()
        with self.assertRaisesRegex(RuntimeError, "tracked input refused"):
            MODULE.source_files(self.root)
        (self.root / "firmware/secrets.yaml").unlink()
        (self.root / "firmware/leak").symlink_to("/absent-private-operator-file")
        self.commit()
        with self.assertRaisesRegex(RuntimeError, "non-regular"):
            MODULE.source_files(self.root)

    def test_wrong_reason_or_successful_negative_cannot_qualify(self):
        for result in (subprocess.CompletedProcess([], 1, b"network down"), subprocess.CompletedProcess([], 0, b"")):

            def broken(cmd, **kw):
                if kw.get("stdout") == subprocess.PIPE:
                    return result
                return self.fake_compile(cmd, **kw)

            with self.assertRaisesRegex(RuntimeError, r"broken generated C\+\+"):
                self.invoke(broken)


class FirmwareCompilerProvenanceTest(unittest.TestCase):
    """Fixture byte identities only; these tests never earn real compile credit."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.packages = Path(self.tmp.name) / "packages"
        self.package = self.packages / "toolchain-xtensa-esp-elf"
        self.bin = self.package / "bin"
        self.bin.mkdir(parents=True)
        self.manifest = self.package / "package.json"
        self.manifest.write_text('{"name":"toolchain-xtensa-esp-elf","version":"fixture-1"}')
        self.names = sorted(
            f"{prefix}-{suffix}"
            for prefix in ("xtensa-esp-elf", "xtensa-esp32-elf", "xtensa-esp32s2-elf", "xtensa-esp32s3-elf")
            for suffix in ("as", "g++", "gcc", "ld")
        )
        for name in self.names:
            (self.bin / name).write_bytes(("fixture " + name).encode())

    def capture(self):
        return MODULE.compiler_provenance(self.packages)

    def test_actual_manifest_and_each_compiler_byte_identity_are_captured(self):
        receipt = self.capture()
        self.assertEqual(receipt["schema"], "verdify-secretless-container-toolchain/v1")
        self.assertTrue(receipt["actual_host"])
        (package,) = receipt["packages"]
        self.assertEqual(package["package_manifest_sha256"], hashlib.sha256(self.manifest.read_bytes()).hexdigest())
        self.assertEqual([b["name"] for b in package["compiler_binaries"]], self.names)
        for binary in package["compiler_binaries"]:
            actual = self.bin / binary["name"]
            self.assertEqual(binary["sha256"], hashlib.sha256(actual.read_bytes()).hexdigest())
            self.assertEqual(binary["bytes"], actual.stat().st_size)

    def test_changed_compiler_bytes_with_same_version_change_evidence(self):
        before = self.capture()["packages"][0]
        (self.bin / self.names[0]).write_bytes(b"changed compiler with unchanged version")
        after = self.capture()["packages"][0]
        self.assertEqual(before["version"], after["version"])
        self.assertEqual(before["package_manifest_sha256"], after["package_manifest_sha256"])
        self.assertNotEqual(before["compiler_binaries"][0]["sha256"], after["compiler_binaries"][0]["sha256"])

    def test_changed_manifest_bytes_with_same_version_change_evidence(self):
        before = self.capture()["packages"][0]
        self.manifest.write_text(self.manifest.read_text() + "\n")
        after = self.capture()["packages"][0]
        self.assertEqual(before["version"], after["version"])
        self.assertNotEqual(before["package_manifest_sha256"], after["package_manifest_sha256"])
        self.assertEqual(before["compiler_binaries"], after["compiler_binaries"])

    def test_missing_compiler_fails_closed(self):
        (self.bin / self.names[0]).unlink()
        with self.assertRaisesRegex(RuntimeError, "compiler binary provenance incomplete"):
            self.capture()

    def test_compiler_symlink_outside_private_packages_fails_before_read(self):
        compiler = self.bin / self.names[0]
        compiler.unlink()
        compiler.symlink_to("/operator-private/compiler-must-not-be-read")
        with self.assertRaisesRegex(RuntimeError, "provenance path escapes private packages"):
            self.capture()
