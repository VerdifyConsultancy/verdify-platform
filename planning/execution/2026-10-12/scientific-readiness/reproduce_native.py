#!/usr/bin/env python3
"""Run committed treatment prefixes through the existing partial native probe.

This bounded source witness exercises the whole committed disturbance corpus.
It cannot qualify the 21 policy consumers outside the compiled control core,
and supplies no compiled-candidate/current-state full-48 recovery evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    script = ROOT / "scripts/component_prefix_replay.py"
    spec = importlib.util.spec_from_file_location("component_prefix_replay", script)
    assert spec and spec.loader
    tool = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = tool
    spec.loader.exec_module(tool)
    profiles = tool.load_profiles(tool.DEFAULT_PROFILES)

    dependencies = subprocess.run(
        ["g++", "-MM", "-I", str(tool.FIRMWARE_LIB), str(tool.INVARIANTS_SOURCE)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    paths = [Path(name) for name in dependencies.split(":", 1)[1].replace("\\\n", " ").split()]
    source_hashes = {str(path.relative_to(ROOT)): sha(path) for path in paths}
    for path in (script, tool.DEFAULT_PROFILES, Path(__file__)):
        source_hashes[str(path.relative_to(ROOT))] = sha(path)

    edges = []
    for target_name in ("moderate", "aggressive"):
        for kind, start_name, end_name, order in (
            ("activation", "baseline", target_name, tool.ACTIVATION_ORDER),
            ("rollback", target_name, "baseline", tool.ROLLBACK_ORDER),
        ):
            edges.append(
                tool.ReplayEdge(
                    edge=f"{kind}/{start_name}-to-{end_name}",
                    kind=kind,
                    order_name=kind,
                    order=order,
                    start_label=start_name,
                    target_label=end_name,
                    start_state=profiles[start_name],
                    target_state=profiles[end_name],
                    complete_bundle=False,
                )
            )

    with tempfile.TemporaryDirectory(prefix="verdify-native-prefix-source-") as temporary:
        probe = tool.build_compiled_interlock(
            corpus=tool.DEFAULT_CORPUS,
            workdir=Path(temporary),
            compiler="g++",
            max_rows=0,
        )
        if not isinstance(probe, tool.CompiledInvariantInterlock) or len(probe.covered_fields) != 27:
            raise RuntimeError("expected current compiled 27/48 probe was not available")
        # Execute once before the ordinary replay so long native work reports
        # progress. replay() reuses only byte-identical complete-state cache keys.
        raw_native_runs = []
        original_run = subprocess.run

        def capture_native(*arguments, **keywords):
            process = original_run(*arguments, **keywords)
            raw_native_runs.append(
                {"exit_code": process.returncode, "stdout": process.stdout, "stderr": process.stderr}
            )
            return process

        with patch.object(tool.subprocess, "run", side_effect=capture_native):
            for edge in edges:
                cases = tool.enumerate_prefixes(
                    edge.start_state,
                    edge.target_state,
                    order=edge.order,
                    order_name=edge.order_name,
                    edge=edge.edge,
                    complete_bundle=False,
                )
                for case in cases:
                    verdict = probe.verdict(case)
                    print(f"{case.case_id}: {verdict.verdict} — {verdict.detail}", flush=True)
            result = tool.replay(edges, interlock=probe)
        if result.all_pass or result.order_revision is not None:
            raise RuntimeError("partial native coverage must never yield a qualified order revision")
        interlock = probe.describe()
        interlock.pop("corpus_path", None)
        payload = {
            "schema": "verdify-639-partial-native-prefix-source-witness-20261012-v1",
            "source_base_revision": "b5bdb3c65b243c230b7864fa81b95f926b2e68ff",
            "source_sha256": source_hashes,
            "committed_compressed_corpus_sha256": sha(tool.DEFAULT_CORPUS),
            "compiler_version": subprocess.run(
                ["g++", "--version"], capture_output=True, text=True, check=True
            ).stdout.strip(),
            "interlock": interlock,
            "all_pass": result.all_pass,
            "qualified_order_revision": result.order_revision,
            "fixed_order_ok": result.fixed_order_ok,
            "idempotent_ok": result.idempotent_ok,
            "lands_exact_ok": result.lands_exact_ok,
            "counts": result.counts(),
            "edges": result.edges,
            "verdicts": [verdict.payload() for verdict in result.verdicts],
            "raw_native_runs": raw_native_runs,
            "scope": "actual native compiled control-core witness for 32 committed treatment prefixes only",
            "limits": [
                "Exactly 27/48 fields are imposed; 21 external ESPHome consumers remain unqualified.",
                "No candidate compiled-default/current-state full-48 recovery edge or device proof is supplied.",
                "Committed historical disturbance rows are not prospective experiment observations.",
                "A lack of covered-core invariant breaches cannot establish full-policy safety or launch readiness.",
            ],
            "production_sql_calls": 0,
            "device_calls": 0,
            "provider_calls": 0,
            "study_schedule_draws": 0,
        }
    args.output.write_text(json.dumps(payload, allow_nan=False, sort_keys=True, indent=2) + "\n")
    print(f"Preserved partial native source witness; qualification={payload['all_pass']}")


if __name__ == "__main__":
    main()
