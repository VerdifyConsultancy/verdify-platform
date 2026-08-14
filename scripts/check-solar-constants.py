#!/usr/bin/env python3
"""check-solar-constants.py — cross-surface drift guard for solar site constants (#393).

The Longmont greenhouse site constants — latitude 40.167, longitude -105.102,
sunrise/sunset zenith 90.833 — are intentionally triplicated (contract:
docs/design/firmware-v2-contract-2026-06-10.md §B1):

  * ingestor/solar.py                                  — the editable SSOT
  * firmware/lib/greenhouse_solar.h                    — on-chip NOAA engine
  * db/migrations/186-noaa-solar-phase-parity.sql      — fn_solar_* helper bodies
  * db/schema.sql                                      — dumped fn_solar_* bodies

This guard parses the numeric VALUES out of every surface (formatting-tolerant:
40.167 == 40.167f == RADIANS(40.167)) and fails if any occurrence diverges from
the canonical values in ingestor/solar.py. It is deliberately read-only: it
never rewrites firmware or SQL sources.

The SQL patterns scan ALL of db/migrations/*.sql, so a future migration that
redefines the fn_solar_* helpers with a drifted literal is caught without
updating this script. Missing files or zero pattern matches are errors too —
the guard must not rot into a silent pass.

Exit 0 = all surfaces agree. Exit 1 = divergence/parse failure; every mismatch
names BOTH locations (the divergent surface and the canonical source).

Run via `make solar-constants-check`; wired into scripts/ci-local.sh (make ci).
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CANONICAL_REL = "ingestor/solar.py"
TOLERANCE = 1e-6

# constant key -> canonical assignment pattern in ingestor/solar.py
_CANONICAL_PATTERNS = {
    "latitude": re.compile(r"^GREENHOUSE_LAT_DEG\s*=\s*(-?\d+(?:\.\d+)?)\s*$", re.M),
    "longitude": re.compile(r"^GREENHOUSE_LON_DEG\s*=\s*(-?\d+(?:\.\d+)?)\s*$", re.M),
    "zenith": re.compile(r"^_ZENITH_DEG\s*=\s*(-?\d+(?:\.\d+)?)\s*$", re.M),
}

# C++ headers (firmware + the byte-identical twin vendored copy).
_HEADER_PATTERNS = {
    "latitude": re.compile(r"GH_LATITUDE_DEG\s*=\s*(-?\d+(?:\.\d+)?)f?\b"),
    "longitude": re.compile(r"GH_LONGITUDE_DEG\s*=\s*(-?\d+(?:\.\d+)?)f?\b"),
    # `const float zenith = 90.833f * PI_F / 180.0f;`
    "zenith": re.compile(r"\bzenith\s*=\s*(-?\d+(?:\.\d+)?)f?\s*\*\s*PI_F"),
}

# plpgsql DECLARE assignments in the fn_solar_* helper bodies. Literal-only:
# `lat_rad := RADIANS(lat)` (a variable, e.g. compute_solar_position) never
# matches, and neither does the unrelated `lat CONSTANT ... := 40.1672`.
_SQL_PATTERNS = {
    "latitude": re.compile(r"lat_rad\s+(?:double\s+precision\s+)?:=\s*RADIANS\(\s*(-?\d+(?:\.\d+)?)\s*\)", re.I),
    "longitude": re.compile(r"lon_deg\s+(?:double\s+precision\s+)?:=\s*(-?\d+(?:\.\d+)?)\s*;", re.I),
    "zenith": re.compile(r"zenith_rad\s+(?:double\s+precision\s+)?:=\s*RADIANS\(\s*(-?\d+(?:\.\d+)?)\s*\)", re.I),
}


@dataclass(frozen=True)
class Occurrence:
    rel_path: str
    line: int
    key: str
    value: float


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _extract(rel_path: str, text: str, patterns: dict[str, re.Pattern[str]]) -> list[Occurrence]:
    found: list[Occurrence] = []
    for key, pattern in patterns.items():
        for match in pattern.finditer(text):
            found.append(Occurrence(rel_path, _line_of(text, match.start()), key, float(match.group(1))))
    return found


def _read(rel_path: str, errors: list[str]) -> str | None:
    path = ROOT / rel_path
    if not path.is_file():
        errors.append(f"MISSING SURFACE: {rel_path} not found under {ROOT}")
        return None
    return path.read_text(encoding="utf-8")


def _canonical(errors: list[str]) -> dict[str, Occurrence]:
    text = _read(CANONICAL_REL, errors)
    if text is None:
        return {}
    canonical: dict[str, Occurrence] = {}
    for key, pattern in _CANONICAL_PATTERNS.items():
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            errors.append(
                f"CANONICAL PARSE FAILURE: expected exactly 1 {key} assignment in {CANONICAL_REL}, found {len(matches)}"
            )
            continue
        match = matches[0]
        canonical[key] = Occurrence(CANONICAL_REL, _line_of(text, match.start()), key, float(match.group(1)))
    return canonical


def _surface_occurrences(errors: list[str]) -> list[Occurrence]:
    occurrences: list[Occurrence] = []

    # (#587: the vendored firmware-twin src copy is retired — the twin image
    # builds from the canonical firmware/lib/greenhouse_solar.h directly.)
    header_rels = [
        "firmware/lib/greenhouse_solar.h",
    ]
    for rel in header_rels:
        text = _read(rel, errors)
        if text is None:
            continue
        found = _extract(rel, text, _HEADER_PATTERNS)
        for key in _HEADER_PATTERNS:
            if not any(o.key == key for o in found):
                errors.append(f"PARSE FAILURE: no {key} literal found in {rel} (guard pattern rotted?)")
        occurrences.extend(found)

    migrations_dir = ROOT / "db" / "migrations"
    if not migrations_dir.is_dir():
        errors.append(f"MISSING SURFACE: db/migrations not found under {ROOT}")
    else:
        migration_found: list[Occurrence] = []
        for sql_path in sorted(migrations_dir.glob("*.sql")):
            rel = sql_path.relative_to(ROOT).as_posix()
            migration_found.extend(_extract(rel, sql_path.read_text(encoding="utf-8"), _SQL_PATTERNS))
        for key in _SQL_PATTERNS:
            if not any(o.key == key for o in migration_found):
                errors.append(
                    f"PARSE FAILURE: no {key} DECLARE literal found in any db/migrations/*.sql "
                    "(expected at least 186-noaa-solar-phase-parity.sql)"
                )
        occurrences.extend(migration_found)

    schema_text = _read("db/schema.sql", errors)
    if schema_text is not None:
        found = _extract("db/schema.sql", schema_text, _SQL_PATTERNS)
        for key in _SQL_PATTERNS:
            if not any(o.key == key for o in found):
                errors.append(f"PARSE FAILURE: no {key} DECLARE literal found in db/schema.sql fn_solar_* bodies")
        occurrences.extend(found)

    return occurrences


def main(argv: list[str] | None = None) -> int:
    del argv  # no options; keeps the CLI shape stable for make/ci wiring
    errors: list[str] = []
    canonical = _canonical(errors)
    occurrences = _surface_occurrences(errors)

    for occ in occurrences:
        canon = canonical.get(occ.key)
        if canon is None:
            continue  # canonical parse failure already reported
        if abs(occ.value - canon.value) > TOLERANCE:
            errors.append(
                f"DRIFT: {occ.rel_path}:{occ.line} {occ.key} = {occ.value!r} "
                f"!= canonical {canon.rel_path}:{canon.line} = {canon.value!r}"
            )

    if errors:
        print("solar site-constant SSOT guard FAILED (#393):", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        print(
            "  fix: edit ingestor/solar.py (the SSOT) and update every mirrored surface "
            "to the same values — see docs/design/firmware-v2-contract-2026-06-10.md §B1",
            file=sys.stderr,
        )
        return 1

    surfaces = sorted({o.rel_path for o in occurrences})
    values = ", ".join(f"{key}={canonical[key].value}" for key in sorted(canonical))
    print(f"solar site constants agree across {len(surfaces)} surfaces ({len(occurrences)} occurrences): {values}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
