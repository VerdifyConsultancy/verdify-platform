"""CLI for the §8.3 qualification settling analyzer (#588).

Subcommands:

  spec-hash   print the SHA-256 of a qualification specification file's exact
              bytes (the hash the experiment row's protocol_sha256 pins)
  settle      run the frozen settling analyzer over an extracted transition
              set and write the machine-readable qualification result +
              result hash (what fn_experiment_transition binds for `aa`)

Run as ``uv run --project research/planner-efficacy python -m qualification ...``
from ``research/planner-efficacy/``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from qualification.settling import EXPECTED_TRANSITIONS, analyze


def _cmd_spec_hash(args: argparse.Namespace) -> int:
    digest = hashlib.sha256(Path(args.spec).read_bytes()).hexdigest()
    print(digest)
    return 0


def _cmd_settle(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    outcome = analyze(payload, expected_transitions=args.expected)
    text = json.dumps(outcome, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    gate = outcome["result"]["gate"]
    print(
        f"gate: {'PASS' if gate['pass'] else 'FAIL'} "
        f"(max settling {gate['max_settling_h']} h over "
        f"{gate['observed_transitions']}/{gate['expected_transitions']} transitions)",
        file=sys.stderr,
    )
    print(f"result_sha256: {outcome['result_sha256']}", file=sys.stderr)
    return 0 if gate["pass"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qualification")
    sub = parser.add_subparsers(dest="command", required=True)

    p_hash = sub.add_parser("spec-hash", help="hash a qualification spec file's bytes")
    p_hash.add_argument("spec")
    p_hash.set_defaults(func=_cmd_spec_hash)

    p_settle = sub.add_parser("settle", help="run the frozen settling analyzer")
    p_settle.add_argument("--input", required=True, help="extracted transitions JSON")
    p_settle.add_argument("--output", help="write the qualification result JSON here")
    p_settle.add_argument(
        "--expected",
        type=int,
        default=EXPECTED_TRANSITIONS,
        help="required transition count (default 96)",
    )
    p_settle.set_defaults(func=_cmd_settle)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
