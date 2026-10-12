#!/usr/bin/env python3
"""Offline metadata regression using a disposable Git clone of frozen source.

No live observations are made. Only external image/Workflow/native observation
functions are replaced by successful test fixtures. Source checks, Git reads,
source hashing, stage control flow, and runtime catalog creation remain real.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

OPTIONS = argparse.ArgumentParser(description=__doc__)
OPTIONS.add_argument("--repository", type=Path, required=True)
OPTIONS.add_argument("--revision", default="d86ecb84ae55d35fbdf395ec9a5c866625bbbde6")
OPTIONS.add_argument("--output", type=Path, required=True)
OPTIONS.add_argument("--expect-corrected", action="store_true")
ARGS = OPTIONS.parse_args()
FROZEN = ARGS.repository.resolve()
EVIDENCE = ARGS.output.resolve().parent
EVIDENCE.mkdir(parents=True, exist_ok=True)
TOOLING = ARGS.revision
RUNTIME = "b874103a3b861537e02d1d2847029f2513c4c183"
RELATIVE = Path("control-plane/agent-fleet-control/scripts/executable_image_delivery.py")
IMAGE_DIGEST = "sha256:" + "c" * 64

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["GIT_OPTIONAL_LOCKS"] = "0"
sys.dont_write_bytecode = True


def git(root, *arguments):
    return subprocess.check_output(["git", "-C", str(root), *arguments], text=True).strip()


def run():
    frozen_status_before = git(FROZEN, "status", "--porcelain")
    frozen_head_before = git(FROZEN, "rev-parse", "HEAD")
    frozen_script_before = hashlib.sha256((FROZEN / RELATIVE).read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="split-runtime-git-", dir=EVIDENCE) as directory:
        tree = Path(directory) / "agents"
        subprocess.run(
            ["git", "clone", "--shared", "--no-hardlinks", "--no-checkout", "--quiet", str(FROZEN), str(tree)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tree), "checkout", "--quiet", "--detach", TOOLING], check=True, capture_output=True
        )
        assert git(tree, "rev-parse", "HEAD") == TOOLING
        assert git(tree, "cat-file", "-t", RUNTIME) == "commit"
        sys.path.insert(0, str((tree / RELATIVE).parent))
        spec = importlib.util.spec_from_file_location("readonly_split_runtime_probe", tree / RELATIVE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Both checks use real Git and actual source bytes in this disposable clone.
        source_contract = module.check_source()
        module._require_committed_image_sources(RUNTIME, ("runtime",))
        module._require_committed_image_sources(TOOLING, ("broker",))
        inventory = module._runtime_source_inventory()
        runtime_catalog = module._runtime_catalog_candidate(
            RUNTIME, {"build_run": "repo-build-runtime-offline-source-audit"}
        )
        historical_hash = hashlib.sha256()
        for relative in inventory:
            historical_bytes = subprocess.check_output(
                ["git", "-C", str(tree), "show", RUNTIME + ":control-plane/agent-fleet-control/" + relative]
            )
            current_bytes = (tree / "control-plane/agent-fleet-control" / relative).read_bytes()
            assert historical_bytes == current_bytes, relative
            historical_hash.update(relative.encode() + b"\0" + historical_bytes + b"\0")
        assert historical_hash.hexdigest() == runtime_catalog["source_sha"]

        catalog_capture = {}
        real_catalog = module._updated_runtime_catalog

        def observe_catalog(revision, release):
            path, document = real_catalog(revision, release)
            image = module.ARTIFACTS["runtime"]["image"] + "@" + release["image_digest"]
            catalog_capture.update({"argument_revision": revision, "entry": copy.deepcopy(document["releases"][image])})
            return path, document

        def workflow(role, _path, revision, **_kwargs):
            return {
                "build_run": "repo-build-" + role + "-offline-source-audit",
                "image_digest": IMAGE_DIGEST,
                "workflow_observation": {"fixture_source_revision": revision},
            }

        arguments = argparse.Namespace(
            source_revision=TOOLING,
            runtime_source_revision=RUNTIME,
            broker_workflow_expectation="offline-broker.json",
            runtime_workflow_expectation="offline-runtime.json",
            native_runtime_workflow_expectation="offline-native.json",
            crane="/offline/nonexecuted-crane",
            write=False,
        )
        with (
            mock.patch.object(module, "_selected_crane_receipt", return_value={"offline_fixture": True}),
            mock.patch.object(module, "_workflow_release", side_effect=workflow),
            mock.patch.object(module, "exported_installed_bytes_digest", return_value="sha256:" + "e" * 64),
            mock.patch.object(module, "_qualify_native_runtime", return_value={"offline_fixture": True}),
            mock.patch.object(module, "_validated_tool_bytes", return_value=None),
            mock.patch.object(module, "_updated_runtime_catalog", side_effect=observe_catalog),
            mock.patch.object(
                module, "_require_committed_image_sources", wraps=module._require_committed_image_sources
            ) as closure,
            mock.patch.object(module, "_write_contract") as write,
        ):
            staged = module.stage(arguments)
            write.assert_not_called()
            source_calls = [[call.args[0], list(call.args[1])] for call in closure.call_args_list]

        assert staged["status"] == "staged"
        accepted = staged["candidate_releases"]["runtime"]["accepted_agents_revision"]
        claimed = catalog_capture["entry"]["source_revision"]
        assert accepted == RUNTIME
        assert claimed == (RUNTIME if ARGS.expect_corrected else TOOLING)
        assert (claimed == accepted) == ARGS.expect_corrected
        assert source_calls.count([RUNTIME, ["runtime"]]) == 3

        # Demonstrate the exact metadata-only call correction without editing source.
        _path, corrected_document = real_catalog(RUNTIME, staged["candidate_releases"]["runtime"])
        image = module.ARTIFACTS["runtime"]["image"] + "@" + IMAGE_DIGEST
        corrected = corrected_document["releases"][image]
        assert corrected["source_revision"] == accepted
        assert corrected["source_sha"] == catalog_capture["entry"]["source_sha"]
        assert corrected["build_run"] == catalog_capture["entry"]["build_run"]
        assert git(tree, "status", "--porcelain") == ""

        result = {
            "scope": "offline source regression; external observations are fixtures, not delivery proof",
            "tooling_revision": TOOLING,
            "requested_runtime_revision": RUNTIME,
            "real_check_source_status": source_contract["status"],
            "real_runtime_closure_check": "passed",
            "runtime_inventory_count": len(inventory),
            "runtime_historical_and_current_inventory_sha256": historical_hash.hexdigest(),
            "runtime_source_bundle": module.source_bundle("runtime"),
            "stage_status": staged["status"],
            "runtime_release_accepted_revision": accepted,
            "runtime_catalog_actual_revision": claimed,
            "catalog_metadata_mismatch_reproduced": claimed != accepted,
            "real_source_checks_during_stage": source_calls,
            "catalog_entry_actual": catalog_capture["entry"],
            "catalog_entry_correct_revision_call": corrected,
            "source_unchanged_in_disposable_clone": True,
            "no_live_calls": True,
            "external_fixture_functions": [
                "_selected_crane_receipt",
                "_workflow_release",
                "exported_installed_bytes_digest",
                "_qualify_native_runtime",
                "_validated_tool_bytes",
            ],
            "retained_boundary": "finalize requires one shared accepted revision; no change proposed",
        }
    assert git(FROZEN, "rev-parse", "HEAD") == frozen_head_before
    assert git(FROZEN, "status", "--porcelain") == frozen_status_before
    assert hashlib.sha256((FROZEN / RELATIVE).read_bytes()).hexdigest() == frozen_script_before
    result.update({"frozen_head_and_tracked_source_unchanged": True, "frozen_script_sha256": frozen_script_before})
    output = ARGS.output.resolve()
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "evidence": str(output),
                "catalog_metadata_mismatch_reproduced": claimed != accepted,
                "runtime_closure_real_git_passed": True,
                "frozen_source_unchanged": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    run()
