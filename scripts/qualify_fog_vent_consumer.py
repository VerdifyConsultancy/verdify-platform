#!/usr/bin/env python3
"""Bounded Linux qualification of the extracted fog/vent consumer.

Run only through the admitted native qualification path. This script does not
submit workloads, fetch dependencies, contact a device or read credentials.
Complete text inputs/results are emitted for custody; binaries are hash-only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from fog_vent_consumer import (
    NEGATIVE_WITNESSES,
    ROOT,
    SCENARIOS,
    SELECTIONS,
    VARIANTS,
    generate_cpp,
    sha256,
)


def require_behavior(result: dict, scenario: str, selection: str, expected: int) -> None:
    completion = f"SCENARIO_COMPLETE name={scenario} selection={selection} exit={expected}\n"
    if result["timed_out"] or result["returncode"] != expected or result["stdout"] != completion:
        raise ValueError("scenario did not complete with its exact expected result")
    if expected == 0:
        if result["stderr"]:
            raise ValueError("unexpected original-scenario stderr")
    elif not re.fullmatch(r"BEHAVIOR_ASSERT selected_consumer_request line=\d+\n", result["stderr"]):
        raise ValueError("negative did not fail the unchanged consumer behavior assertion")


def run_process(
    command: list[str], *, cwd: Path, out: Path, name: str, timeout: float, deadline: float, records: list[dict]
) -> dict:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("qualification overall deadline exhausted")
    start = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(command, cwd=cwd, capture_output=True, timeout=min(timeout, remaining), check=False)
        stdout, stderr, code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code = exc.stdout or b"", exc.stderr or b"", None
        timed_out = True
    (out / f"{name}.stdout").write_bytes(stdout)
    (out / f"{name}.stderr").write_bytes(stderr)
    record = {
        "name": name,
        "command": command,
        "returncode": code,
        "timed_out": timed_out,
        "elapsed_s": round(time.monotonic() - start, 6),
        "stdout_bytes": len(stdout),
        "stdout_sha256": sha256(stdout),
        "stderr_bytes": len(stderr),
        "stderr_sha256": sha256(stderr),
    }
    records.append(record)
    (out / "process-results.json").write_text(json.dumps(records, indent=2) + "\n")
    print(json.dumps({"process_result": record}), flush=True)
    return dict(record, stdout=stdout.decode("utf-8"), stderr=stderr.decode("utf-8"))


def emit_artifacts(out: Path) -> None:
    files = [path for path in sorted(out.rglob("*")) if path.is_file()]
    if sum(path.stat().st_size for path in files) > 16 * 1024 * 1024:
        raise ValueError("text custody exceeds declared 16 MiB bound; retain output directory")
    manifest = []
    for path in files:
        data = path.read_bytes()
        entry = {"path": str(path.relative_to(out)), "bytes": len(data), "sha256": sha256(data)}
        manifest.append(entry)
        print(json.dumps({"artifact": entry, "base64": base64.b64encode(data).decode()}), flush=True)
    print(json.dumps({"artifact_manifest": manifest}), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if platform.system() != "Linux" or os.environ.get("VERDIFY_NATIVE_QUALIFICATION") != "1":
        parser.error("requires the admitted Linux native qualification workload; never compile on the Mac")
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_source):
        parser.error("expected source must be a full Git commit")
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    deadline = time.monotonic() + 600
    records: list[dict] = []
    binaries = []
    receipt = {
        "format": "verdify-fog-vent-native-v1",
        "status": "FAILED",
        "expected_source": args.expected_source,
        "platform": platform.platform(),
        "python": sys.version,
        "deadline_s": 600,
        "original_expected": 56,
        "negative_expected": 8,
        "original_passed": 0,
        "negative_completed": 0,
        "binary_artifacts": [],
        "firmware_changed": False,
        "corpus_prefix_coverage": "27/48 UNPROVEN; no row-wise credit added",
        "physical_acceptance": False,
        "device_or_database_calls": 0,
    }

    def run(command: list[str], name: str, timeout: float = 5) -> dict:
        return run_process(command, cwd=ROOT, out=out, name=name, timeout=timeout, deadline=deadline, records=records)

    try:
        source = run(["git", "rev-parse", "HEAD"], "source-head")
        if source["returncode"] or source["stdout"].strip() != args.expected_source:
            raise ValueError("native checkout differs from exact admitted source")
        status = run(["git", "status", "--porcelain", "--untracked-files=no"], "source-status")
        if status["returncode"] or status["stdout"]:
            raise ValueError("native source checkout has tracked modifications")
        compiler = shutil.which("c++")
        if not compiler:
            raise ValueError("supported image has no existing C++ compiler")
        resolved = Path(compiler).resolve()
        receipt["compiler"] = {
            "path": compiler,
            "resolved_path": str(resolved),
            "sha256": sha256(resolved.read_bytes()),
        }
        for flag, name in (("--version", "compiler-version"), ("-dumpmachine", "compiler-target")):
            result = run([compiler, flag], name)
            if result["returncode"] or result["timed_out"]:
                raise ValueError("compiler provenance failed")
        receipt["compiler_programs"] = []
        for program in ("cc1plus", "as", "ld"):
            result = run([compiler, f"-print-prog-name={program}"], f"compiler-program-{program}")
            value = result["stdout"].strip()
            located = value if "/" in value else shutil.which(value)
            if result["returncode"] or result["timed_out"] or not located or not Path(located).is_file():
                raise ValueError("compiler program provenance failed")
            path = Path(located).resolve()
            receipt["compiler_programs"].append(
                {
                    "name": program,
                    "path": str(path),
                    "sha256": sha256(path.read_bytes()),
                }
            )
        # Preserve input bytes before the first compile, including on failure.
        _, manifest = generate_cpp(ROOT)
        input_paths = {row["path"] for row in manifest["source_files"]}
        input_paths.update(
            {
                "scripts/fog_vent_consumer.py",
                "scripts/qualify_fog_vent_consumer.py",
                "tests/fixtures/fog_vent_consumer.cpp.in",
                "tests/test_fog_vent_consumer.py",
            }
        )
        for relative in sorted(input_paths):
            target = out / "source-inputs" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        for variant in VARIANTS:
            cpp, manifest = generate_cpp(ROOT, variant)
            manifest["source_commit"] = args.expected_source
            (out / f"{variant}.source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            source_path, binary = out / f"{variant}.cpp", out / f"{variant}.bin"
            source_path.write_text(cpp)
            binaries.append(binary)
            result = run(
                [compiler, "-std=c++17", "-O0", "-I", str(ROOT / "firmware/lib"), str(source_path), "-o", str(binary)],
                f"compile-{variant}",
                120,
            )
            if result["returncode"] != 0 or result["timed_out"] or not binary.is_file():
                raise ValueError("compile failure cannot qualify a causal behavioral negative")
            receipt["binary_artifacts"].append(
                {
                    "variant": variant,
                    "bytes": binary.stat().st_size,
                    "sha256": sha256(binary.read_bytes()),
                    "retention": "digest only; nondeployable native fixture",
                }
            )
            scenarios = SCENARIOS if variant == "original" else NEGATIVE_WITNESSES
            expected = 0 if variant == "original" else 1
            for scenario in scenarios:
                for selection in SELECTIONS:
                    result = run([str(binary), scenario, selection], f"{variant}-{scenario}-{selection}")
                    require_behavior(result, scenario, selection, expected)
                    key = "original_passed" if expected == 0 else "negative_completed"
                    receipt[key] += 1
            # Only reviewed source text and result text leave the scratch area.
            binary.unlink()
        if (receipt["original_passed"], receipt["negative_completed"]) != (56, 8):
            raise ValueError("incomplete native matrix")
        receipt["status"] = "PASSED"
        receipt["meaning"] = "actual extracted single-consumer synthetic matrix and two completed causal mutants"
    except Exception as exc:
        receipt["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        for binary in binaries:
            binary.unlink(missing_ok=True)
        receipt["process_count"] = len(records)
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        emit_artifacts(out)
        print(json.dumps({"qualification_result": receipt}), flush=True)
    return 0 if receipt["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
