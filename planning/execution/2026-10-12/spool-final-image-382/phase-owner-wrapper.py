"""Finite serial phase wrapper; accepts only its immutable owner stage release."""

import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path


def validate_release(packet, release, phase):
    manifest = json.loads((packet / "fixture-manifest.json").read_text())
    binding = json.loads((packet / "native-binding.json").read_text())

    def sha(name):
        return hashlib.sha256((packet / name).read_bytes()).hexdigest()

    assert release["allowed"] is True and release["phase"] == phase
    assert release["run_id"] == manifest["run_id"]
    assert release["manifest_sha256"] == sha("fixture-manifest.json")
    assert release["binding_sha256"] == sha("native-binding.json")
    assert release["helper_sha256"] == sha("qualify-ingestor-committed-spool.py")
    for key in ("cluster_uid", "primary_uid", "rw_service_uid", "database_oid", "system_identifier"):
        assert release[key] == binding[key]
    assert release["sole_target_writer"] == "/root/runtime_inventory"
    assert release["fresh_capacity_fit"] is True and release["fresh_isolation_verified"] is True
    assert release["fresh_baseline_exact"] is True and release["previous_phase_accepted"] is True
    assert os.environ["QUALIFICATION_BINDING"] == (packet / "native-binding.json").read_text()
    return manifest


def main():
    packet = Path("/qualification/packet")
    phase = sys.argv[1]
    release = json.loads(Path("/qualification/release/owner-stage-release.json").read_text())
    manifest = validate_release(packet, release, phase)
    start = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            str(packet / "qualify-ingestor-committed-spool.py"),
            "--manifest",
            str(packet / "fixture-manifest.json"),
            "--phase",
            phase,
        ],
        check=False,
    )
    expected = 74 if phase.startswith("crash-") else 0
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    # Linux KiB; this wrapper is staged only inside the pinned Linux image.
    print(
        json.dumps(
            {
                "phase": phase,
                "run_id": manifest["run_id"],
                "native_process_exit_code": result.returncode,
                "expected_process_exit_code": expected,
                "elapsed_seconds": time.monotonic() - start,
                "child_peak_rss_kib": peak,
                "child_exit_expected": result.returncode == expected,
                "postgres_commit_requires_owner_native_witness": True,
                "receipt_is_not_phase_acceptance": True,
            }
        ),
        flush=True,
    )
    sys.exit(0 if result.returncode == expected else 1)


if __name__ == "__main__":
    main()
