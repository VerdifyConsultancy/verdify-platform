#!/usr/bin/env python3
"""Frozen-FSM baseline candidate builder (Lane G tranche 2 of #588; audit §8.2).

Single source of truth for:

1. the read-only extraction SQL (``emit-sql``) that ``extract-baseline.sh``
   pipes to the production DB — a time-weighted value histogram of the
   ``setpoint_snapshot`` effective readbacks for every policy-wire parameter
   (48 under wire schema v2) over the §8.2 window;
2. the baseline candidate artifact (``build``) — per-field time-weighted
   medians (numeric) / time-weighted modes (switches), quantized through the
   canonical wire schema in ``verdify_schemas.policy_vector``;
3. the two AI template candidates (``build-templates``) — moderate and
   aggressive hot/dry response vectors that differ from the baseline only in
   the §8.2 11-field allowlist; and
4. ``requantize`` — rebuild the artifact under the CURRENT wire schema from a
   committed artifact's per-field raw statistics, WITHOUT touching the
   database. Contract v2 (#588) retired ``direct_wet_stress_latest_hour``
   (the only unqualified v1 field — zero device readbacks by construction),
   so requantizing the committed v1 artifact yields a fully-qualified 48-field
   vector. The original extraction block (SQL + input-CSV hashes) is carried
   VERBATIM for provenance, and a ``provenance.requantized_schema_version``
   note records the operation.

§8.2 contract implemented here:

- Window: Denver-local days 2026-07-12 .. 2026-08-04 inclusive, excluding the
  2026-07-25 reboot day. Weight = the duration each readback value was in
  effect inside the window (intervals from consecutive snapshots, clipped to
  the window, with the excluded day zero-weighted).
- Numeric fields take the time-weighted median; switches take the modal
  (time-weighted majority) value; both are then quantized round-half-even
  onto the canonical wire grid.
- A field with no qualified readback in the window is listed as UNQUALIFIED
  and blocks approval; no default is silently substituted. The canonical
  vector bytes / content hash are only emitted when all 48 fields qualify.

The artifact status stays CANDIDATE until horticultural, firmware, and safety
owners approve the complete vector after compiled replay, HIL, and A/A
(gate:jason). Raw extraction CSVs contain operational posture only in
aggregate but still follow the research convention: they stay outside Git;
only the derived JSON artifacts are committed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# verdify_schemas is imported from the repo root (same convention as the
# switchback cross-check tests: path shim, no packaging change outside
# research/planner-efficacy/**).
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas.policy_vector import (
    content_sha256,
    encode_policy_vector,
    quantize_policy_values,
    wire_fields,
    wire_manifest_digest,
)
from verdify_schemas.tunable_registry import WIRE_SCHEMA_VERSION, TunableDef

GREENHOUSE_ID = "vallery"
TIMEZONE = "America/Denver"
# Local-day window per §8.2: 2026-07-12 .. 2026-08-04 inclusive (end exclusive
# below), excluding the 2026-07-25 reboot day.
WINDOW_START_LOCAL = "2026-07-12 00:00:00"
WINDOW_END_LOCAL = "2026-08-05 00:00:00"
EXCLUDE_START_LOCAL = "2026-07-25 00:00:00"
EXCLUDE_END_LOCAL = "2026-07-26 00:00:00"
# 24 local days minus the excluded day (no DST transition inside the window).
EFFECTIVE_WINDOW_SECONDS = 23 * 86400
# Lookback for the carry-in row: the last readback at or before window start
# defines the value in effect from window start to the first in-window row.
CARRY_LOOKBACK = "3 days"

# Fixed policy revision ids for content_sha256 — identical to the Lane A
# golden fixtures (scripts/gen-policy-vector-goldens.py REVISION_IDS): the
# wire-v2 registry revision (#588 retired wire_id 6) and the commit that
# locked the v1 wire schema. test_baseline cross-checks the equality.
POLICY_REVISION_IDS = {"registry_rev": "wire-v2-retire-wire-id-6", "schema_rev": "efa85343"}

STATUS = "CANDIDATE — pending horticultural/firmware/safety approval (gate:jason)"
BASELINE_ARTIFACT_NAME = "frozen-fsm-baseline-candidate-2026-08-14.json"
TEMPLATES_ARTIFACT_NAME = "ai-template-candidates-2026-08-14.json"

# §8.2 first-trial allowlist: the only fields that may differ between the
# Frozen-FSM baseline (arm A) and an AI template (arm B).
DIFF_ALLOWLIST = (
    "cool_stage2_over_high_f",
    "sw_cool_all_fans_at_high_enabled",
    "fog_escalation_kpa",
    "min_fog_on_s",
    "min_fog_off_s",
    "mister_engage_kpa",
    "mister_all_kpa",
    "mister_all_delay_s",
    "mister_pulse_gap_s",
    "mister_pulse_on_s",
    "mister_water_budget_gal",
)

# ── Template designs ─────────────────────────────────────────────────────────
# Grounded in the §5 effective-readback posture table (epoch means over
# 47,956 sampled minutes) and the §5 forecast-response correlations. Values
# are chosen on the wire grid and inside BOTH the registry planner bounds and
# the firmware clamp bounds (validated at build time).
#
# moderate ≈ the observed epoch-mean posture, rounded onto the wire grid;
# aggressive = one bounded step further in the responsive (earlier/stronger
# hot-dry response) direction, never touching a bound the epoch never used
# except where the epoch itself already sat near it.
MODERATE_DESIGN: dict[str, dict] = {
    "cool_stage2_over_high_f": {
        "value": 0.8,
        "evidence": "§5 epoch mean 0.786°F (57.7% below the 1.0°F default)",
        "rationale": "Second-stage cooling engages slightly earlier than default, matching observed practice.",
    },
    "sw_cool_all_fans_at_high_enabled": {
        "value": True,
        "evidence": "§5: enabled 58.3% of sampled minutes (default off)",
        "rationale": "Majority-time posture ran both fans immediately above temp_high.",
    },
    "fog_escalation_kpa": {
        "value": 0.3,
        "evidence": "§5 epoch mean 0.261 kPa (75.4% below the 0.4 kPa default); wire grid 0.1 kPa",
        "rationale": "Earlier fog escalation than default; 0.3 is the nearest wire-grid value to the epoch mean.",
    },
    "min_fog_on_s": {
        "value": 60.0,
        "evidence": "no §5 evidence of movement from the 60 s default",
        "rationale": "Held at the compiled default; the observed epoch did not demonstrate a different posture.",
    },
    "min_fog_off_s": {
        "value": 60.0,
        "evidence": "no §5 evidence of movement from the 60 s default",
        "rationale": "Held at the compiled default; the observed epoch did not demonstrate a different posture.",
    },
    "mister_engage_kpa": {
        "value": 1.2,
        "evidence": "§5 epoch mean 1.185 kPa (99.96% below the 1.6 kPa default); wire grid 0.05 kPa",
        "rationale": "Physical mister pulses engage well before the default threshold, matching observed practice.",
    },
    "mister_all_kpa": {
        "value": 1.35,
        "evidence": "§5 epoch mean 1.360 kPa (99.99% below the 1.9 kPa default); wire grid 0.05 kPa",
        "rationale": "All-zone rotation threshold near the epoch mean.",
    },
    "mister_all_delay_s": {
        "value": 90.0,
        "evidence": "§5 epoch mean 87.3 s (>99.9% below the 300 s default); registry notes stress default 60-90 s",
        "rationale": "Short all-zone dwell consistent with the observed stress posture, on the 30 s operator step.",
    },
    "mister_pulse_gap_s": {
        "value": 38.0,
        "evidence": "§5 epoch mean 37.6 s (69.9% below the 45 s default)",
        "rationale": "Slightly shorter evaporation dwell between zone pulses, matching observed practice.",
    },
    "mister_pulse_on_s": {
        "value": 60.0,
        "evidence": "§5 posture table records no epoch mean shift; forecast response (+0.604 Pearson vs future max VPD) is plan-conditional",
        "rationale": "Held at the compiled default; per-forecast lengthening belongs to the aggressive template.",
    },
    "mister_water_budget_gal": {
        "value": 220.0,
        "evidence": "§5 epoch mean 222.4 gal (57.5% below the 300 gal default; never above)",
        "rationale": "Lower total water ceiling with earlier response — the observed efficiency strategy.",
    },
}

AGGRESSIVE_DESIGN: dict[str, dict] = {
    "cool_stage2_over_high_f": {
        "value": 0.5,
        "evidence": "§5 Pearson −0.540 (stage-2 offset vs future max VPD): earlier second-stage cooling under load",
        "rationale": "One bounded step earlier than the moderate 0.8°F; well inside the [0, 3]°F clamp.",
    },
    "sw_cool_all_fans_at_high_enabled": {
        "value": True,
        "evidence": "§5: enabled 58.3% of sampled minutes",
        "rationale": "Both fans immediately above temp_high, as in the moderate template.",
    },
    "fog_escalation_kpa": {
        "value": 0.2,
        "evidence": "§5 epoch mean 0.261 kPa already 75.4% below default",
        "rationale": "One 0.1 kPa wire step earlier than moderate; stays above the 0.1 kPa clamp floor.",
    },
    "min_fog_on_s": {
        "value": 90.0,
        "evidence": "§5: longer wet pulses under severe forecasts (mister pulse-on Pearson +0.604)",
        "rationale": "Longer minimum fog runs once engaged, consistent with the longer-wet-pulse response direction.",
    },
    "min_fog_off_s": {
        "value": 30.0,
        "evidence": "§5: shorter recovery gaps under severe forecasts (pulse-gap Pearson −0.596)",
        "rationale": "Faster fog re-engagement during escalation; stays above the 15 s clamp floor.",
    },
    "mister_engage_kpa": {
        "value": 1.0,
        "evidence": "§5 epoch mean 1.185 kPa; observed posture spent 99.96% of time below default",
        "rationale": "One bounded step earlier than moderate; twice the 0.5 kPa clamp floor.",
    },
    "mister_all_kpa": {
        "value": 1.2,
        "evidence": "§5 epoch mean 1.360 kPa; registry notes keep it close to active vpd_high under hot/dry stress",
        "rationale": "Earlier all-zone assist; stays above the 1.0 kPa clamp floor.",
    },
    "mister_all_delay_s": {
        "value": 60.0,
        "evidence": "§5 epoch mean 87.3 s; registry stress default range 60-90 s",
        "rationale": "Fastest permitted all-zone rotation — exactly the 60 s firmware clamp floor the epoch already approached.",
    },
    "mister_pulse_gap_s": {
        "value": 30.0,
        "evidence": "§5: shorter recovery gaps under severe forecasts (Pearson −0.596, partial −0.446)",
        "rationale": "One bounded step below the moderate 38 s; three times the 10 s clamp floor.",
    },
    "mister_pulse_on_s": {
        "value": 75.0,
        "evidence": "§5: longer wet pulses under severe forecasts (Pearson +0.604, partial +0.493)",
        "rationale": "Halfway between the 60 s default and the 90 s clamp ceiling.",
    },
    "mister_water_budget_gal": {
        "value": 250.0,
        "evidence": "§5: more water headroom on severe forecasts (budget vs future max VPD Pearson +0.683)",
        "rationale": "More headroom than moderate for severe hot/dry days while keeping a ceiling below the 300 gal default.",
    },
}


# ── SQL ──────────────────────────────────────────────────────────────────────


def build_sql() -> str:
    """Read-only extraction SQL: time-weighted value histogram per wire field.

    One row per (parameter, distinct readback value) with the number of
    readback intervals and the total seconds that value was in effect inside
    the §8.2 window. Interval = consecutive-snapshot gap clipped to the
    window; the excluded reboot day is zero-weighted (an interval spanning
    the exclusion contributes only its time outside it). A carry-in row
    (latest readback at or before window start) covers the head of the
    window. All current wire-schema parameters (48 under v2) are queried; a
    parameter with no rows is UNQUALIFIED downstream. NOTE: the committed
    2026-08-14 artifact was extracted with the v1 (49-parameter) form of this
    SQL and preserves that exact text in its extraction block; ``requantize``
    keeps it verbatim.
    """
    params = ",\n  ".join(f"('{d.name}')" for d in wire_fields())
    return f"""BEGIN READ ONLY;
SET LOCAL statement_timeout='300s';
COPY (
WITH bounds AS (
  SELECT (timestamp '{WINDOW_START_LOCAL}' AT TIME ZONE '{TIMEZONE}') AS w_start,
         (timestamp '{WINDOW_END_LOCAL}' AT TIME ZONE '{TIMEZONE}') AS w_end,
         (timestamp '{EXCLUDE_START_LOCAL}' AT TIME ZONE '{TIMEZONE}') AS x_start,
         (timestamp '{EXCLUDE_END_LOCAL}' AT TIME ZONE '{TIMEZONE}') AS x_end
), params(parameter) AS (VALUES
  {params}
), in_window AS (
  SELECT s.parameter, s.ts, s.value
    FROM setpoint_snapshot s
    JOIN params USING (parameter)
   CROSS JOIN bounds b
   WHERE s.greenhouse_id = '{GREENHOUSE_ID}'
     AND s.value IS NOT NULL
     AND s.ts >= b.w_start AND s.ts < b.w_end
), carry AS (
  SELECT DISTINCT ON (s.parameter) s.parameter, b.w_start AS ts, s.value
    FROM setpoint_snapshot s
    JOIN params USING (parameter)
   CROSS JOIN bounds b
   WHERE s.greenhouse_id = '{GREENHOUSE_ID}'
     AND s.value IS NOT NULL
     AND s.ts < b.w_start AND s.ts >= b.w_start - interval '{CARRY_LOOKBACK}'
   ORDER BY s.parameter, s.ts DESC
), snaps AS (
  SELECT parameter, ts, value FROM in_window
  UNION ALL
  SELECT parameter, ts, value FROM carry
), seq AS (
  SELECT parameter, value, ts AS ivl_start,
         lead(ts) OVER (PARTITION BY parameter ORDER BY ts) AS next_ts
    FROM snaps
), ivl AS (
  SELECT s.parameter, s.value,
         extract(epoch FROM (coalesce(least(s.next_ts, b.w_end), b.w_end) - s.ivl_start))
         - greatest(0.0, extract(epoch FROM (
             least(coalesce(least(s.next_ts, b.w_end), b.w_end), b.x_end)
             - greatest(s.ivl_start, b.x_start)))) AS weight_s
    FROM seq s
   CROSS JOIN bounds b
)
SELECT parameter, value,
       count(*) AS interval_count,
       sum(weight_s) AS coverage_s
  FROM ivl
 WHERE weight_s > 0
 GROUP BY parameter, value
 ORDER BY parameter, value
) TO STDOUT WITH CSV HEADER;
COMMIT;
"""


def sql_sha256() -> str:
    return hashlib.sha256(build_sql().encode("utf-8")).hexdigest()


# ── Aggregation ──────────────────────────────────────────────────────────────


def time_weighted_median(pairs: list[tuple[float, float]]) -> float:
    """Weighted median of (value, weight) pairs.

    Smallest value whose cumulative weight reaches half the total; when the
    cumulative weight lands exactly on half, the midpoint to the next value
    is used (quantization then snaps it to the wire grid).
    """
    if not pairs:
        raise ValueError("time_weighted_median of empty histogram")
    if any(w <= 0 for _, w in pairs):
        raise ValueError("non-positive weight in histogram")
    ordered = sorted(pairs)
    total = sum(w for _, w in ordered)
    target = total / 2.0
    cum = 0.0
    for i, (value, weight) in enumerate(ordered):
        cum += weight
        if cum > target:
            return value
        if cum == target:
            return (value + ordered[i + 1][0]) / 2.0
    return ordered[-1][0]  # pragma: no cover — float guard


def time_weighted_mode(pairs: list[tuple[float, float]]) -> float:
    """Value with the largest total weight; deterministic lower-value tie-break."""
    if not pairs:
        raise ValueError("time_weighted_mode of empty histogram")
    if any(w <= 0 for _, w in pairs):
        raise ValueError("non-positive weight in histogram")
    return max(pairs, key=lambda p: (p[1], -p[0]))[0]


def _registry_placeholder(defn: TunableDef) -> float | bool:
    return bool(defn.default) if defn.wire_kind == "bool" else float(defn.default)


def quantize_field(name: str, value: float | bool) -> float | bool:
    """Quantize one field through the canonical 48-field codec path.

    ``quantize_policy_values`` requires all 48 fields, so the other 47 are
    filled with registry defaults purely to run the canonical codec; only the
    requested field's quantized value is returned and the placeholders never
    reach any artifact.
    """
    filler: dict[str, float | bool] = {d.name: _registry_placeholder(d) for d in wire_fields()}
    filler[name] = value
    return quantize_policy_values(filler)[name]


# ── Baseline artifact ────────────────────────────────────────────────────────


def read_histogram(csv_path: Path) -> tuple[dict[str, list[tuple[float, float, int]]], int]:
    """Parse the extraction CSV into {parameter: [(value, coverage_s, intervals)]}."""
    histogram: dict[str, list[tuple[float, float, int]]] = {}
    rows = 0
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows += 1
            histogram.setdefault(row["parameter"], []).append(
                (float(row["value"]), float(row["coverage_s"]), int(row["interval_count"]))
            )
    return histogram, rows


def build_artifact(csv_path: Path, generated_at: str | None = None) -> dict:
    histogram, csv_rows = read_histogram(csv_path)
    csv_sha = hashlib.sha256(csv_path.read_bytes()).hexdigest()

    fields: dict[str, dict] = {}
    unqualified: list[str] = []
    quantized_values: dict[str, float | bool] = {}
    for defn in wire_fields():
        pairs = [(value, seconds) for value, seconds, _ in histogram.get(defn.name, [])]
        entry: dict = {
            "wire_id": defn.wire_id,
            "kind": defn.kind,
            "wire_kind": defn.wire_kind,
            "statistic": "time_weighted_mode" if defn.wire_kind == "bool" else "time_weighted_median",
            "interval_count": sum(n for _, _, n in histogram.get(defn.name, [])),
            "coverage_seconds": round(sum(seconds for _, seconds in pairs), 3),
            "distinct_values": len(pairs),
        }
        if not pairs:
            entry["qualified"] = False
            entry["reason"] = "no qualified readback in window"
            unqualified.append(defn.name)
        else:
            raw = time_weighted_mode(pairs) if defn.wire_kind == "bool" else time_weighted_median(pairs)
            entry["raw_value"] = raw
            try:
                quantized = quantize_field(defn.name, bool(raw) if defn.wire_kind == "bool" else raw)
            except ValueError as exc:
                entry["qualified"] = False
                entry["reason"] = f"raw statistic does not quantize onto the wire envelope: {exc}"
                unqualified.append(defn.name)
            else:
                entry["qualified"] = True
                entry["quantized_value"] = quantized
                quantized_values[defn.name] = quantized
        fields[defn.name] = entry

    artifact: dict = {
        "artifact": "frozen-fsm-baseline-candidate",
        "status": STATUS,
        "issue": "#588",
        "epic": "#581",
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "method": {
            "source_table": "setpoint_snapshot",
            "greenhouse_id": GREENHOUSE_ID,
            "timezone": TIMEZONE,
            "window_local_days": {"first": "2026-07-12", "last": "2026-08-04"},
            "window_local": {"start": WINDOW_START_LOCAL, "end_exclusive": WINDOW_END_LOCAL},
            "excluded_local_days": ["2026-07-25"],
            "exclusion_reason": "reboot day (§8.2)",
            "effective_window_seconds": EFFECTIVE_WINDOW_SECONDS,
            "carry_in_lookback": CARRY_LOOKBACK,
            "numeric_statistic": "time-weighted median (weight = seconds each readback value was in effect)",
            "switch_statistic": "time-weighted mode",
            "quantization": "verdify_schemas.policy_vector.quantize_policy_values (round-half-even onto the wire grid)",
            "unqualified_policy": "a field with no qualified readback blocks approval; no default is substituted",
        },
        "extraction": {
            "sql_sha256": sql_sha256(),
            "input_csv_sha256": csv_sha,
            "input_csv_data_rows": csv_rows,
            "sql": build_sql(),
        },
        "wire_schema": {
            "version": WIRE_SCHEMA_VERSION,
            "field_count": len(fields),
            "manifest_digest_sha256": wire_manifest_digest().hex(),
        },
        "fields": fields,
        "unqualified_fields": unqualified,
        "policy_revision_ids": POLICY_REVISION_IDS,
    }

    _attach_canonical_vector(artifact, unqualified, quantized_values)
    return artifact


def _attach_canonical_vector(artifact: dict, unqualified: list[str], quantized_values: dict[str, float | bool]) -> None:
    if unqualified:
        artifact["canonical_vector"] = {
            "omitted": True,
            "reason": (
                f"unqualified fields block the complete {len(artifact['fields'])}-field vector: "
                + ", ".join(unqualified)
            ),
        }
    else:
        vector = encode_policy_vector(quantized_values)
        artifact["canonical_vector"] = {
            "omitted": False,
            "vector_hex": vector.hex(),
            "content_sha256": content_sha256(
                vector, schema_version=WIRE_SCHEMA_VERSION, policy_revision_ids=POLICY_REVISION_IDS
            ).hex(),
        }


# ── Requantization (contract v2, #588) ───────────────────────────────────────


def build_requantized_artifact(source: dict, generated_at: str | None = None) -> dict:
    """Rebuild the baseline candidate under the CURRENT wire schema from a
    committed artifact — per-field raw medians/modes only, NO database access.

    The source's method + extraction blocks (window definition, original SQL
    text, SQL/input-CSV hashes) are carried VERBATIM; every current wire field
    takes its committed raw statistic and is re-quantized through the current
    codec. Fields the current schema no longer contains (retired, e.g.
    ``direct_wet_stress_latest_hour``) are dropped and recorded in the
    provenance note.
    """
    source_fields = source["fields"]
    current = {defn.name for defn in wire_fields()}
    missing = sorted(current - set(source_fields))
    if missing:
        raise ValueError(f"source artifact lacks per-field data for current wire fields: {missing}")
    dropped = sorted(set(source_fields) - current)

    fields: dict[str, dict] = {}
    unqualified: list[str] = []
    quantized_values: dict[str, float | bool] = {}
    for defn in wire_fields():
        src = source_fields[defn.name]
        entry: dict = {
            "wire_id": defn.wire_id,
            "kind": defn.kind,
            "wire_kind": defn.wire_kind,
            "statistic": src["statistic"],
            "interval_count": src["interval_count"],
            "coverage_seconds": src["coverage_seconds"],
            "distinct_values": src["distinct_values"],
        }
        if "raw_value" not in src:
            entry["qualified"] = False
            entry["reason"] = src.get("reason", "no qualified readback in window")
            unqualified.append(defn.name)
        else:
            raw = src["raw_value"]
            entry["raw_value"] = raw
            try:
                quantized = quantize_field(defn.name, bool(raw) if defn.wire_kind == "bool" else raw)
            except ValueError as exc:
                entry["qualified"] = False
                entry["reason"] = f"raw statistic does not quantize onto the wire envelope: {exc}"
                unqualified.append(defn.name)
            else:
                entry["qualified"] = True
                entry["quantized_value"] = quantized
                quantized_values[defn.name] = quantized
        fields[defn.name] = entry

    artifact: dict = {
        "artifact": source["artifact"],
        "status": STATUS,
        "issue": source["issue"],
        "epic": source["epic"],
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "provenance": {
            "requantized_schema_version": WIRE_SCHEMA_VERSION,
            "requantized_from_generated_at": source["generated_at"],
            "source_wire_schema": source["wire_schema"],
            "retired_fields_dropped": dropped,
            "note": (
                "Re-quantized from the committed artifact's per-field raw time-weighted "
                "statistics under wire schema v2 (#588 retired direct_wet_stress_latest_hour, "
                "the sole unqualified v1 field — zero device readbacks by construction). "
                "No database access; the extraction block below is the ORIGINAL v1 SQL and "
                "input hashes, preserved verbatim."
            ),
        },
        "method": source["method"],
        "extraction": source["extraction"],
        "wire_schema": {
            "version": WIRE_SCHEMA_VERSION,
            "field_count": len(fields),
            "manifest_digest_sha256": wire_manifest_digest().hex(),
        },
        "fields": fields,
        "unqualified_fields": unqualified,
        "policy_revision_ids": POLICY_REVISION_IDS,
    }
    _attach_canonical_vector(artifact, unqualified, quantized_values)
    return artifact


# ── Template artifact ────────────────────────────────────────────────────────


def _bounds_ok(defn: TunableDef, value: float | bool) -> bool:
    if defn.wire_kind == "bool":
        return isinstance(value, bool)
    numeric = float(value)
    for lo, hi in ((defn.min, defn.max), (defn.fw_clamp_lo, defn.fw_clamp_hi)):
        if lo is not None and numeric < lo:
            return False
        if hi is not None and numeric > hi:
            return False
    return True


def build_templates_artifact(baseline: dict, generated_at: str | None = None) -> dict:
    by_name = {d.name: d for d in wire_fields()}
    designs = {"moderate": MODERATE_DESIGN, "aggressive": AGGRESSIVE_DESIGN}
    templates: dict[str, dict] = {}
    for template_name, design in designs.items():
        if set(design) != set(DIFF_ALLOWLIST):
            raise ValueError(f"{template_name} design must cover exactly the 11-field allowlist")
        entries: dict[str, dict] = {}
        for name in DIFF_ALLOWLIST:
            defn = by_name[name]
            value = design[name]["value"]
            if not _bounds_ok(defn, value):
                raise ValueError(f"{template_name}.{name}={value!r} violates registry/firmware bounds")
            quantized = quantize_field(name, value)
            if quantized != value:
                raise ValueError(
                    f"{template_name}.{name}={value!r} is not on the wire grid (quantizes to {quantized!r})"
                )
            baseline_field = baseline["fields"][name]
            baseline_quantized = baseline_field.get("quantized_value")
            entries[name] = {
                "wire_id": defn.wire_id,
                "value": quantized,
                "baseline_quantized_value": baseline_quantized,
                "differs_from_baseline": baseline_quantized != quantized,
                "registry_bounds": [defn.min, defn.max],
                "firmware_clamp": [defn.fw_clamp_lo, defn.fw_clamp_hi],
                "evidence": design[name]["evidence"],
                "rationale": design[name]["rationale"],
            }
        templates[template_name] = {
            "design_intent": (
                "epoch-mean hot/dry posture on the wire grid"
                if template_name == "moderate"
                else "bounded step further in the responsive hot/dry direction"
            ),
            "fields": entries,
        }

    artifact: dict = {
        "artifact": "ai-template-candidates",
        "status": STATUS,
        "issue": "#588",
        "epic": "#581",
        "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        "baseline_artifact": BASELINE_ARTIFACT_NAME,
        "baseline_sql_sha256": baseline["extraction"]["sql_sha256"],
        "baseline_input_csv_sha256": baseline["extraction"]["input_csv_sha256"],
        "diff_allowlist": list(DIFF_ALLOWLIST),
        "inheritance_rule": (
            "all 37 wire fields outside the allowlist are byte-identical to the approved "
            "Frozen-FSM baseline vector (§8.2; wire schema v2 has 48 fields)"
        ),
        "unresolved_baseline_fields": baseline["unqualified_fields"],
        "wire_schema": baseline["wire_schema"],
        "policy_revision_ids": POLICY_REVISION_IDS,
        "templates": templates,
    }

    if "provenance" in baseline:
        artifact["baseline_provenance"] = baseline["provenance"]

    if baseline["unqualified_fields"] or baseline["canonical_vector"].get("omitted", True):
        artifact["canonical_vectors"] = {
            "omitted": True,
            "reason": (
                "template vectors inherit 37 fields from the baseline, which is incomplete: "
                + ", ".join(baseline["unqualified_fields"])
            ),
        }
    else:
        vectors: dict[str, dict] = {}
        base_values = {name: field["quantized_value"] for name, field in baseline["fields"].items()}
        for template_name, template in templates.items():
            values = dict(base_values)
            for name, entry in template["fields"].items():
                values[name] = entry["value"]
            blob = encode_policy_vector(values)
            vectors[template_name] = {
                "vector_hex": blob.hex(),
                "content_sha256": content_sha256(
                    blob, schema_version=WIRE_SCHEMA_VERSION, policy_revision_ids=POLICY_REVISION_IDS
                ).hex(),
            }
        artifact["canonical_vectors"] = {"omitted": False, **vectors}
    return artifact


# ── CLI ──────────────────────────────────────────────────────────────────────


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("emit-sql", help="print the canonical extraction SQL")
    build = sub.add_parser("build", help="build the baseline candidate artifact from the extraction CSV")
    build.add_argument("--input", required=True, type=Path, help="baseline_intervals.csv from extract-baseline.sh")
    build.add_argument("--out", required=True, type=Path)
    templates = sub.add_parser("build-templates", help="build the AI template candidates artifact")
    templates.add_argument("--baseline", required=True, type=Path, help="committed baseline candidate JSON")
    templates.add_argument("--out", required=True, type=Path)
    requantize = sub.add_parser(
        "requantize",
        help="rebuild a committed baseline artifact under the current wire schema (no DB access)",
    )
    requantize.add_argument("--source", required=True, type=Path, help="committed baseline candidate JSON")
    requantize.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.command == "emit-sql":
        sys.stdout.write(build_sql())
    elif args.command == "build":
        _write_json(args.out, build_artifact(args.input))
        print(f"baseline candidate written to {args.out}")
    elif args.command == "build-templates":
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        _write_json(args.out, build_templates_artifact(baseline))
        print(f"template candidates written to {args.out}")
    elif args.command == "requantize":
        source = json.loads(args.source.read_text(encoding="utf-8"))
        _write_json(args.out, build_requantized_artifact(source))
        print(f"requantized baseline candidate written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
