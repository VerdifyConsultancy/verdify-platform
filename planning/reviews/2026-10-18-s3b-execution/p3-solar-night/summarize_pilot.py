"""Summarize <=500 retained episode rows without replacing their SQL verdicts.

No DB/network calls. Missing/overflow/failed output is rejected, not interpreted
as zero events. This does not certify deployment, planner consumption or crops.
"""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def summarize(raw, identity):
    require(len(raw) <= 1048576, "pilot output exceeds 1 MiB")
    lines = [json.loads(line) for line in raw.splitlines() if line.strip()]
    require(len(lines) == 2, "expected guard plus complete episode envelope")
    guard, result = lines
    require(guard["kind"] == "pilot_identity_guard" and guard["passed"] == 1, "identity guard failed")
    require(guard["read_only"] == "on" and guard["database"] == "verdify", "wrong DB transaction")
    require(guard["postmaster_started_at"] == identity["context"]["postmaster_started_at"], "postmaster changed")
    night = identity["preselected_pilot_night"]
    for row in lines:
        require(row["identity_receipt_sha256"] == identity["identity_sha256"], "identity receipt mismatch")
        require(row["night_date"] == night, "preselected night mismatch")
    require(result["kind"] == "episodes" and result["greenhouse_id"] == "vallery", "wrong result scope")
    require(result["row_cap"] == 500 and result["row_cap_exceeded"] is False, "truncated result")
    episodes = result["episodes"]
    require(isinstance(episodes, list) and len(episodes) <= 500, "episode limit exceeded")
    require(len(episodes) == result["episode_rows_returned"], "episode count mismatch")
    require(all(row["night_date"] == night for row in episodes), "unexpected episode date")
    require(len({row["episode_id"] for row in episodes}) == len(episodes), "duplicate episode")
    safety = [row["safety_gate_status"] for row in episodes]
    aggregate = (
        "pending"
        if not safety
        else "fail"
        if "fail" in safety
        else "pass"
        if all(value == "pass" for value in safety)
        else "incomplete"
    )

    def counts(key):
        return dict(sorted(Counter(str(row[key]) for row in episodes).items()))

    def maximum(key):
        values = [row[key] for row in episodes]
        return max(values) if values and all(isinstance(value, int) for value in values) else None

    return {
        "status": "BOUNDED_EPISODE_READBACK" if episodes else "NO_EPISODE_ROWS",
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "identity_receipt_sha256": identity["identity_sha256"],
        "night_date": night,
        "window_start": result["window_start"],
        "window_end": result["window_end"],
        "episode_count": len(episodes),
        "aggregate_safety_status": aggregate,
        "dispositions": counts("dryout_disposition"),
        "evidence_statuses": counts("evidence_status"),
        "admission_statuses": counts("admission_status"),
        "daytime_dry_action_samples": maximum("daytime_dry_action_samples"),
        "daytime_hold_admission_samples": maximum("daytime_hold_admission_samples"),
        "daytime_count_semantics": "Adjacent local dates repeated by SQL; maximum, never sum. No rows means unknown.",
        "episodes": episodes,
        "limitations": [
            "Calendar-complete bounds do not certify whole-night telemetry coverage.",
            "SQL episode coverage/confounds/outcomes are preserved, not upgraded.",
            "Historical setpoint readback is preferred; the getter can fall back to current crop-band sources.",
            "No universal VPD/crop-safety, deployed MCP/planner-consumption or physical-control acceptance.",
        ],
        "whole_issue_acceptance": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.raw.read_bytes(), json.loads(args.identity.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
