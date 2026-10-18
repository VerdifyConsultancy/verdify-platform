"""One admitted k3s compiler/replay pilot. Do not run on the operator Mac.

This checks native source/assignment/refusal plumbing only. It cannot qualify
any order revision, active executor route, recovery scenario or physical action.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("prefix_pilot_tool", ROOT / "scripts/component_prefix_replay.py")
assert SPEC and SPEC.loader
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


def retained_run(command, **kwargs):
    """Emit each native result before any later assertion can fail.

    These subprocesses consume committed source/corpus only, never private DB
    values or credentials. ROOT must retain the complete native stdout stream.
    """
    try:
        result = _SUBPROCESS_RUN(command, **kwargs)
    except subprocess.TimeoutExpired as exc:
        record = {"command": command, "exit_code": None, "failure": "TimeoutExpired"}
        for name in ("stdout", "stderr"):
            value = getattr(exc, name) or ""
            record[name] = value.decode(errors="replace") if isinstance(value, bytes) else value
        print("PREFIX_PILOT_PROCESS_RESULT=" + json.dumps(record, sort_keys=True), flush=True)
        raise
    record = {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "stdout_sha256": tool._sha256_bytes(result.stdout.encode()),
        "stderr_sha256": tool._sha256_bytes(result.stderr.encode()),
    }
    print("PREFIX_PILOT_PROCESS_RESULT=" + json.dumps(record, sort_keys=True), flush=True)
    return result


_SUBPROCESS_RUN = subprocess.run


def main() -> None:
    source = tool.source_coverage_report()
    assert source["declared_assignment_count"] == 27 and len(source["unimposed_fields"]) == 21
    baseline = tool.load_profiles(tool.DEFAULT_PROFILES)["baseline"]
    with tempfile.TemporaryDirectory(prefix="prefix-source-pilot-") as temporary:
        work = Path(temporary)
        probe = tool.build_compiled_interlock(corpus=tool.DEFAULT_CORPUS, workdir=work, compiler="g++", max_rows=128)
        assert isinstance(probe, tool.CompiledInvariantInterlock), probe.describe()
        assert probe.row_count == 128
        case = tool.PrefixCase("bounded-baseline-witness", "activation", 0, baseline, (), ())
        outcome = probe.verdict(case)
        # An unusable corpus or compiler/invariant failure must not be silently
        # accepted merely because all of them also yield an unproven verdict.
        assert outcome.verdict == tool.UNPROVEN and outcome.detail.startswith("no invariant breach"), outcome
        incomplete = work / "incomplete-policy.txt"
        incomplete.write_text(
            "".join(f"{name} {probe._cell(baseline[name])}\n" for name in tool.CANONICAL_FIELD_ORDER[:-1])
        )
        negative = subprocess.run(
            [str(probe.binary), str(probe.corpus_path), f"--policy-state={incomplete}"],
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert negative.returncode == 64, (negative.returncode, "incomplete policy was not rejected")
        receipt = {
            "schema": "verdify-component-prefix-source-pilot-v1",
            "source_assignment_contract_passed": True,
            "coverage": probe.describe(),
            "baseline_case": {
                "verdict": outcome.verdict,
                "detail": outcome.detail,
                "evidence_sha256": outcome.evidence_sha256,
            },
            "missing_field_negative": {
                "exit_code": negative.returncode,
                "stdout_sha256": tool._sha256_bytes(negative.stdout.encode()),
                "stderr_sha256": tool._sha256_bytes(negative.stderr.encode()),
            },
            "native_harness_behavior_invocations": 2,
            "native_harness_process_invocations": 3,
            "compiler_process_invocations": 1,
            "process_output_custody": "complete PREFIX_PILOT_PROCESS_RESULT lines in retained native stdout",
            "binary_custody": "digest only; private temporary compiled binary removed after run",
            "corpus_rows": 128,
            "full_prefix_qualification": False,
            "physical_qualification": False,
            "order_revision": None,
        }
        print(json.dumps(receipt, sort_keys=True), flush=True)


if __name__ == "__main__":
    subprocess.run = retained_run
    try:
        main()
    finally:
        subprocess.run = _SUBPROCESS_RUN
