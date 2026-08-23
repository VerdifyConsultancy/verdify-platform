"""CLI for the switchback randomization and commitment tooling.

Subcommands mirror the Section 8.3 ceremony:

  gen-schedule    generate the blinded schedule JSON and print its RFC8785 hash
  commit-mapping  read an existing 32-byte mapping secret file and print the
                  publishable commitment hex (this verification CLI does not
                  generate it; the restricted assignment service will)
  verify          recompute the schedule from beacon inputs (and optionally the
                  commitment from the secret) and diff against published artifacts
  reveal          resolve the blinded schedule to physical arms for analysis lock

Run as ``uv run --project research/planner-efficacy python -m switchback ...``
from ``research/planner-efficacy/``.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from switchback.randomization import (
    MAPPING_SECRET_LENGTH,
    blinded_schedule,
    mapping_commitment,
    resolve_schedule,
    rfc8785_sha256,
)


def _read_beacon(args: argparse.Namespace) -> bytes:
    if args.beacon_hex is not None:
        return bytes.fromhex(args.beacon_hex)
    return Path(args.beacon_file).read_bytes()


def _read_secret(path: str) -> bytes:
    secret = Path(path).read_bytes()
    if len(secret) != MAPPING_SECRET_LENGTH:
        raise SystemExit(
            f"mapping secret file must contain exactly {MAPPING_SECRET_LENGTH} raw bytes, got {len(secret)}"
        )
    return secret


def _add_beacon_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--beacon-file", help="file containing the raw beacon output bytes")
    group.add_argument("--beacon-hex", help="beacon output bytes as hex")


def cmd_gen_schedule(args: argparse.Namespace) -> int:
    schedule = blinded_schedule(
        args.study_id,
        args.start_local_date,
        beacon_bytes=_read_beacon(args),
        namespace_uuid=uuid.UUID(args.namespace_uuid),
        timezone=args.timezone,
        pairs=args.pairs,
    )
    Path(args.out).write_text(json.dumps(schedule, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.out}")
    print(f"schedule_hash_sha256 {schedule['schedule_hash_sha256']}")
    return 0


def cmd_commit_mapping(args: argparse.Namespace) -> int:
    secret = _read_secret(args.secret_file)
    print(mapping_commitment(args.study_id, secret).hex())
    return 0


def cmd_reveal(args: argparse.Namespace) -> int:
    schedule = json.loads(Path(args.schedule).read_text())
    resolved = resolve_schedule(schedule, _read_secret(args.secret_file))
    Path(args.out).write_text(json.dumps(resolved, indent=2, sort_keys=True) + "\n")
    print(f"wrote {args.out}")
    print(f"arm_mapping {resolved['arm_mapping']}")
    print(f"mapping_commitment_sha256 {resolved['mapping_commitment_sha256']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    published = json.loads(Path(args.schedule).read_text())
    blinded = published.get("blinded_assignment", published)
    failures: list[str] = []

    recomputed = blinded_schedule(
        blinded["study_id"],
        blinded["start_local_date"],
        beacon_bytes=_read_beacon(args),
        namespace_uuid=uuid.UUID(blinded["namespace_uuid"]),
        timezone=blinded["timezone"],
        pairs=blinded["pairs"],
    )
    if recomputed["blinded_assignment"] != blinded:
        failures.append("recomputed blinded assignment differs from published document")
        for expected, actual in zip(
            recomputed["blinded_assignment"]["assignments"], blinded["assignments"], strict=False
        ):
            if expected != actual:
                failures.append(
                    f"  first differing day {actual.get('local_date')}: expected {expected}, published {actual}"
                )
                break

    published_hash = published.get("schedule_hash_sha256")
    actual_hash = rfc8785_sha256(blinded).hex()
    if published_hash is not None and published_hash != actual_hash:
        failures.append(f"published schedule hash {published_hash} != recomputed {actual_hash}")
    if recomputed["schedule_hash_sha256"] != actual_hash:
        failures.append("beacon-derived schedule hash differs from published document hash")

    if args.secret_file is not None:
        secret = _read_secret(args.secret_file)
        commitment = mapping_commitment(blinded["study_id"], secret).hex()
        if args.commitment is not None and commitment != args.commitment.lower():
            failures.append(f"mapping commitment {commitment} != published {args.commitment.lower()}")
    elif args.commitment is not None:
        failures.append("--commitment given without --secret-file; cannot verify")

    if failures:
        for line in failures:
            print(f"FAIL {line}", file=sys.stderr)
        return 1
    print(f"OK schedule_hash_sha256 {actual_hash}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="switchback", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("gen-schedule", help="generate the blinded schedule JSON")
    gen.add_argument("--study-id", required=True)
    gen.add_argument("--start-local-date", required=True, help="first local day, YYYY-MM-DD")
    gen.add_argument("--namespace-uuid", required=True, help="the protocol's fixed namespace UUID")
    gen.add_argument("--timezone", default="America/Denver")
    gen.add_argument("--pairs", type=int, default=15)
    gen.add_argument("--out", required=True, help="output path for the blinded schedule JSON")
    _add_beacon_arguments(gen)
    gen.set_defaults(func=cmd_gen_schedule)

    commit = sub.add_parser("commit-mapping", help="print the commitment for an existing 32-byte secret file")
    commit.add_argument("--study-id", required=True)
    commit.add_argument(
        "--secret-file", required=True, help="file with exactly 32 raw secret bytes (not generated by this CLI)"
    )
    commit.set_defaults(func=cmd_commit_mapping)

    verify = sub.add_parser("verify", help="recompute everything and diff against published artifacts")
    verify.add_argument("--schedule", required=True, help="published blinded schedule JSON path")
    verify.add_argument("--secret-file", help="optional mapping secret file, to verify the commitment")
    verify.add_argument("--commitment", help="published commitment hex to check against the secret")
    _add_beacon_arguments(verify)
    verify.set_defaults(func=cmd_verify)

    reveal = sub.add_parser("reveal", help="produce the resolved physical A/B schedule")
    reveal.add_argument("--schedule", required=True, help="blinded schedule JSON path")
    reveal.add_argument("--secret-file", required=True)
    reveal.add_argument("--out", required=True, help="output path for the resolved schedule JSON")
    reveal.set_defaults(func=cmd_reveal)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
