"""Regenerate source-only protocol-v2 profiles, power, and canonical goldens.

No network, database, provider, cluster, or device client is used.  The
schedule secret below is a conspicuous deterministic test vector, never an
operational randomization secret.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path

from switchback.v2_analysis import paired_upper_bound
from switchback.v2_power import PowerAssumptions, SelectorReplay, build_power_artifact, choose_fixed_pairs
from switchback.v2_profiles import build_profile_artifact
from switchback.v2_randomization import (
    DesignLock,
    blinded_schedule,
    canonical_schedule_bytes,
    full_entropy_commitment,
    hidden_mapping,
    schedule_schema_contract_sha256,
)

SYNTHETIC_GOLDEN_SECRET = bytes(range(32))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def generate(repo_root: Path, *, check: bool = False) -> list[Path]:
    research = repo_root / "research/planner-efficacy"
    profile_path = research / "baseline/planner-switchback-v2-profiles.json"
    power_path = research / "protocols/planner-switchback-v2-power.json"
    schedule_path = research / "protocols/blinded-schedule-v2.golden.json"
    analyzer_path = research / "protocols/analyzer-interface-v2.golden.json"
    outputs: dict[Path, dict] = {profile_path: build_profile_artifact(repo_root)}

    assumptions = PowerAssumptions(
        paired_sd=(0.12198766625845635, 0.43532608025564046, 1062.9947787716267),
        true_nonbaseline_effect=(0.0, 0.0, -404.5981795560847),
        selector_replay=SelectorReplay(baseline=25, moderate=50, aggressive=20, fallback=5),
        correlation=((1.0, 0.45, -0.25), (0.45, 1.0, -0.30), (-0.25, -0.30, 1.0)),
        complete_pair_probability=0.9995,
    )
    selection = choose_fixed_pairs(assumptions, repetitions=25_000, seed=588_639)
    source_result = research / "results-current-firmware-supplement-2026-08-14.json"
    power_module = research / "switchback/v2_power.py"
    generator = research / "generate_v2_artifacts.py"
    artifact = build_power_artifact(
        assumptions,
        selection,
        source_files_sha256={
            str(source_result.relative_to(repo_root)): _sha256(source_result),
            str(power_module.relative_to(repo_root)): _sha256(power_module),
            str(generator.relative_to(repo_root)): _sha256(generator),
        },
        status="PROVISIONAL PRE-DRAW SCENARIO — not eligible for randomization lock",
        limitations=[
            "The checked-in historical paired scales use [02:00,24:00) and 88 bins; raw data are not in Git, so the required [06:00,24:00) 72-bin scales must be regenerated before design lock.",
            "Provider/model/prompt are not frozen and no pretrial selector replay exists; the 25/50/20/5 baseline/moderate/aggressive/fallback counts are an explicit planning assumption, not observed selection frequency.",
            "The cross-endpoint correlation matrix is a plausible planning model, not an estimate from randomized or arm-resolved outcomes.",
            "Complete-pair probability 0.9995 is a reliability target assumption and must be replaced by source-bound six-hour climate/equipment completeness evidence.",
            "The nonbaseline benefit effect is an assumed 20 percent reduction from the historical 2022.9908977804234-minute mean; it is not a causal effect estimate.",
            "The resulting 150-pair choice applies only to this provisional model. A final fixed m must be chosen once after the missing pre-draw inputs are frozen and before any schedule draw; it may never adapt to randomized outcomes.",
        ],
    )
    outputs[power_path] = artifact

    design = DesignLock(
        study_id="verdify-v2-golden",
        start_local_date="2026-09-01",
        timezone="America/Denver",
        pairs=2,
        assignment_namespace_uuid=uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8"),
        design_lock_sha256="11" * 32,
        source_git_sha="8f9e011b8e186c3b4e735130d837eefe9a079b12",
        schedule_schema_sha256=schedule_schema_contract_sha256(),
    )
    schedule = blinded_schedule(design, SYNTHETIC_GOLDEN_SECRET)
    schedule_bytes = canonical_schedule_bytes(schedule)
    schedule_hash = hashlib.sha256(schedule_bytes).digest()
    outputs[schedule_path] = {
        "schema": "verdify-switchback-v2-randomization-golden",
        "version": 1,
        "test_vector_only": True,
        "synthetic_secret_hex": SYNTHETIC_GOLDEN_SECRET.hex(),
        "schedule_schema_contract_sha256": schedule_schema_contract_sha256(),
        "canonical_schedule_utf8_hex": schedule_bytes.hex(),
        "canonical_schedule_sha256": schedule_hash.hex(),
        "full_entropy_commitment_sha256": full_entropy_commitment(
            design.study_id, schedule_hash, SYNTHETIC_GOLDEN_SECRET
        ).hex(),
        "hidden_mapping": hidden_mapping(SYNTHETIC_GOLDEN_SECRET, design.study_id),
        "schedule": schedule,
    }
    outputs[analyzer_path] = {
        "schema": "verdify-switchback-v2-analyzer-golden",
        "version": 1,
        "input": {"boundary": 0.0, "pair_contrasts": [-3.0, -2.0, -1.0]},
        "expected": paired_upper_bound([-3.0, -2.0, -1.0], 0.0),
        "hand_calculation": "mean=-2; sample_sd=1; standard_error=1/sqrt(3); upper=-2+t(.975,2)/sqrt(3)",
    }

    drift: list[Path] = []
    for path, value in outputs.items():
        rendered = _render(value)
        if check:
            if not path.is_file() or path.read_bytes() != rendered:
                drift.append(path)
        else:
            path.write_bytes(rendered)
    if drift:
        relative = ", ".join(str(path.relative_to(repo_root)) for path in drift)
        raise ValueError(f"checked-in v2 artifacts differ from deterministic regeneration: {relative}")
    return list(outputs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--check", action="store_true", help="compare all artifacts without writing")
    args = parser.parse_args()
    try:
        paths = generate(args.repo_root.resolve(), check=args.check)
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
