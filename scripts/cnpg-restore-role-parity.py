"""Fail-closed password-free role custody for an isolated CNPG import.

Never change a CNPG management identity. The only possible source collision is
postgres, whose complete CREATE/ALTER statements must already match exactly.
Memberships and grants are compared without normalization.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

MANAGEMENT = {"postgres", "streaming_replica", "rehearsal_bootstrap"}
DIRECT = re.compile(r"^(?:CREATE|ALTER) ROLE (?:\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*))(?:;| )")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(text):
    lines = [line for line in text.splitlines() if line and not line.startswith(("\\restrict ", "\\unrestrict "))]
    require(not any(" PASSWORD " in line for line in lines), "password-bearing role artifact refused")
    return lines


def role(line):
    match = DIRECT.match(line)
    return (match[1] or match[2]) if match else None


def prepare(source, current):
    source, current = canonical(source), canonical(current)
    existing = {role(line) for line in current if line.startswith("CREATE ROLE ")}
    require(existing <= MANAGEMENT and "postgres" in existing, "target contains non-management roles")
    require(not any(role(line) in MANAGEMENT - {"postgres"} for line in source), "management/source collision")
    if any(role(line) == "postgres" for line in source):
        require(
            [line for line in source if role(line) == "postgres"]
            == [line for line in current if role(line) == "postgres"],
            "postgres posture mismatch",
        )
    # Preserve the artifact verbatim except exactly proven identical management
    # CREATE/ALTER commands. Do not replay source privileges onto managed roles.
    return "\n".join(line for line in source if role(line) != "postgres") + "\n"


def verify(source, restored):
    source, restored = canonical(source), canonical(restored)
    names = {role(line) for line in source if line.startswith("CREATE ROLE ")}
    extras = MANAGEMENT - names
    filtered = [line for line in restored if role(line) not in extras]
    require(sorted(source) == sorted(filtered), "restored role posture/settings/membership drift")
    actual_names = {role(line) for line in restored if line.startswith("CREATE ROLE ")}
    return {"source_roles": len(names), "separately_enumerated_management_roles": sorted(actual_names - names)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    source, current = args.source.read_text(), args.current.read_text()
    if args.replay:
        with args.replay.open("x") as stream:
            stream.write(prepare(source, current))
    else:
        print(verify(source, current))


if __name__ == "__main__":
    main()
