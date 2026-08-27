#!/usr/bin/env python3
"""component_prefix_replay.py — Tool B: compiled-prefix replay qualification.

Proves that EVERY intermediate device state ("prefix") of every permitted
component transition, applied in the fixed source order, is grid-valid,
clamp-valid and safety-admissible — and derives the ``ORDER_REVISION`` string
that `verdify_schemas.component_executor.physical_execution_qualified` demands
(together with a separately-qualified live grid revision from Tool A).

This tool is OFFLINE and NON-ACTUATING by construction.  It never opens a
socket, never touches PostgreSQL, never invokes kubectl, and never speaks to a
device.  Its only subprocess is a host C++ compiler plus the compiled firmware
replay harness in ``firmware/test/`` (see the interlock section below), and its
only inputs are committed artifacts plus operator-supplied JSON files.

It is the downstream half of ``scripts/prepare_component_prefix_replay.py``:
that tool enumerates the case list and leaves ``result_slots`` empty; this tool
adjudicates every case and, only when every case passes every check, emits the
qualified revision.  Case ids are deliberately spelled identically in both
tools so the packet and the verdicts can be joined.

Edge set (the permitted directed transitions):

  (a) treatment edges over ``TREATMENT_FIELD_ORDER`` (activation order ==
      rollback order): baseline→moderate, moderate→baseline,
      baseline→aggressive, aggressive→baseline.  Every experiment boundary
      interposes baseline, so those four are the complete permitted set; the
      change list is the actual difference list from
      ``fixed_order_differences``.
  (b) full-48 recovery edges over ``RECOVERY_ORDER``: from the compiled
      ESPHome defaults and from every supplied observed / reboot / reset /
      common-drift start → baseline, built with
      ``fixed_order_complete_bundle`` (unconditional, because reboot/reset
      state is unknown and replaying only apparent differences would preserve
      an unobserved wrong component).

Pass contract — ALL of these must hold, for every case, or the run fails
closed with a non-zero exit code and NO ``order_revision``:

  1. grid          — ``normalize_complete_state`` accepts every prefix state;
  2. fixed order   — the change lists were built from the exact source order
                     tuples and the ``fixed_order_*`` contracts raised nothing;
  3. clamps        — every prefix value is inside BOTH the registry (planner)
                     bounds and the firmware clamp bounds;
  4. interlock     — the compiled firmware harness admits the prefix state
                     (see below);
  5. idempotency   — re-deriving work from an already-confirmed prefix yields
                     exactly the pending suffix and never re-issues a
                     confirmed component;
  6. lands-exact   — each edge's final prefix state is byte-identical to
                     ``normalize_complete_state(target)``.

INTERLOCK / SAFETY ADMISSIBILITY
--------------------------------
The compiled harness driven here is the same one behind ``make
firmware-invariants``: ``firmware/test/replay_invariants.cpp`` compiled with a
host C++17 compiler and run over the tracked disturbance corpus
``firmware/test/data/replay_overrides.csv.gz``, which reconstructs the firmware
decision for every corpus row from ``greenhouse_logic.h`` and asserts the
behavioural invariants in ``firmware/test/invariants.h`` (#1..#27, the same set
``make firmware-invariants`` gates on).

The harness accepts a complete 48-field out-of-band policy state, but only 27
fields have consumers in the compiled decision path.  It prints one
machine-readable coverage record naming those 27 effective assignments and the
21 explicitly unimposed fields.  This tool derives the ceiling from the
compiled binary's policy template, verifies the record says every credited
field was held on every evaluated row, and adjudicates on that arithmetic:

  * a breach  → ``interlock_safe = "unsafe"``  (definitive failure);
  * no breach AND coverage == all 48 fields → ``interlock_safe = "safe"``;
  * no breach AND partial coverage → ``interlock_safe = "unproven"``.

A partial-coverage run can FALSIFY a prefix state but cannot certify one, so
"unproven" blocks ``order_revision`` exactly like a failure.  When the harness
grows the missing ``sp_*`` columns (or an external full-coverage / HIL runner
is supplied with ``--interlock external --interlock-harness PATH``) the
verdicts flip to real "safe" values with no change to this tool.  There is no
flag that turns an unproven interlock into a pass.

CLI shape mirrors scripts/experiment-verify.py and scripts/experiment-aa-gates.py:
one PASS/FAIL/WARN line per check, ``--json`` for the machine-readable payload,
and a canonical result hash (sha256 over the sorted-key compact JSON of the
payload, excluding the hash itself and any wall-clock field).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas.component_executor import (  # noqa: E402
    ACTIVATION_ORDER,
    CANONICAL_FIELD_ORDER,
    COMMON_FIELDS,
    RECOVERY_ORDER,
    ROLLBACK_ORDER,
    TREATMENT_FIELD_ORDER,
    ComponentChange,
    ComponentContractError,
    fixed_order_complete_bundle,
    fixed_order_differences,
    normalize_complete_state,
    validate_routine_target,
)
from verdify_schemas.policy_vector import decode_policy_vector  # noqa: E402
from verdify_schemas.tunable_registry import REGISTRY  # noqa: E402

SCHEMA_VERSION = "verdify-component-prefix-replay-verdict-v1"
ORDER_REVISION_PREFIX = "prefix-replay-v1"
# Must stay identical to component_executor._QUALIFIED_ORDER_REVISION; asserted
# at import so a regex drift here cannot mint a string the executor rejects.
_QUALIFIED_ORDER_REVISION = re.compile(r"^prefix-replay-v[1-9][0-9]*:sha256:[0-9a-f]{64}$")

DEFAULT_PROFILES = REPO_ROOT / "research/planner-efficacy/baseline/planner-switchback-v2-profiles.json"
DEFAULT_CONSUMER_MANIFEST = REPO_ROOT / "firmware/policy_consumer_manifest.json"
DEFAULT_CORPUS = REPO_ROOT / "firmware/test/data/replay_overrides.csv.gz"
INVARIANTS_SOURCE = REPO_ROOT / "firmware/test/replay_invariants.cpp"
FIRMWARE_LIB = REPO_ROOT / "firmware/lib"

PROFILE_NAMES = ("baseline", "moderate", "aggressive")
TREATMENT_EDGES = (
    ("baseline", "moderate"),
    ("moderate", "baseline"),
    ("baseline", "aggressive"),
    ("aggressive", "baseline"),
)
# The only orders an edge may be replayed against — the executor's own tuples.
SOURCE_ORDERS: dict[str, tuple[str, ...]] = {
    "activation": ACTIVATION_ORDER,
    "recovery": RECOVERY_ORDER,
    "rollback": ROLLBACK_ORDER,
}

SAFE = "safe"
UNSAFE = "unsafe"
UNPROVEN = "unproven"

# `sp_*` struct members in the replay harness whose spelling differs from the
# canonical executor component name.  Empty for replay_invariants.cpp today —
# kept so a harness that grows `sp_watch_dwell_s`-style columns is picked up by
# the coverage parser instead of silently dropping out of the coverage count.
SP_MEMBER_ALIASES: dict[str, str] = {
    "watch_dwell_s": "vpd_watch_dwell_s",
    "cool_all_fans_at_high_enabled": "sw_cool_all_fans_at_high_enabled",
}
_SP_ASSIGN = re.compile(r'assign_[a-z_]*\(\s*"(?P<column>sp_[a-z0-9_]+)"\s*,\s*sp\.(?P<member>[a-z0-9_]+)')


class PrefixReplayError(ValueError):
    """The offline inputs cannot produce a truthful replay verdict."""


# ──────────────────────────────────────────────────────────────────────────────
# Canonical JSON / hashing (same convention as scripts/experiment-aa-gates.py)
# ──────────────────────────────────────────────────────────────────────────────


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def result_sha256(payload: Mapping[str, Any]) -> str:
    """sha256 over the sorted-key compact JSON, minus the hash and wall clock."""
    hashed = {k: v for k, v in payload.items() if k not in ("result_sha256", "computed_at")}
    return hashlib.sha256(canonical_json(hashed).encode()).hexdigest()


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def prefix_state_sha256(state: Mapping[str, float | bool]) -> str:
    """Stable identity of the exact complete state adjudicated at one prefix."""
    ordered = {name: state[name] for name in CANONICAL_FIELD_ORDER}
    return _sha256_bytes(canonical_json(ordered).encode())


# ──────────────────────────────────────────────────────────────────────────────
# Clamp bounds
#
# Single bounds source: the tunable registry, exactly as
# research/planner-efficacy/baseline/baseline.py::_bounds_ok reads it —
# (defn.min, defn.max) is the registry/planner contract bound and
# (defn.fw_clamp_lo, defn.fw_clamp_hi) is the dispatcher+firmware clamp that CI
# drift-checks against firmware/greenhouse/tunables.yaml.  `_bounds_ok` folds
# the two into one boolean; the prefix contract needs them adjudicated
# separately, so they are split here and a unit test asserts the conjunction is
# still exactly `_bounds_ok`.
# ──────────────────────────────────────────────────────────────────────────────


def _within(value: float | bool, lower: float | None, upper: float | None) -> bool:
    if isinstance(value, bool):
        return True  # switches have no numeric envelope; the grid check owns their type
    numeric = float(value)
    if lower is not None and numeric < lower:
        return False
    return not (upper is not None and numeric > upper)


def registry_clamp_ok(field_name: str, value: float | bool) -> bool:
    """True iff `value` is inside the registry (planner contract) bounds."""
    definition = REGISTRY[field_name]
    return _within(value, definition.min, definition.max)


def firmware_clamp_ok(field_name: str, value: float | bool) -> bool:
    """True iff `value` is inside the dispatcher+firmware clamp bounds."""
    definition = REGISTRY[field_name]
    return _within(value, definition.fw_clamp_lo, definition.fw_clamp_hi)


def _clamp_violations(state: Mapping[str, float | bool], checker) -> tuple[str, ...]:
    return tuple(f"{name}={state[name]!r}" for name in CANONICAL_FIELD_ORDER if not checker(name, state[name]))


# ──────────────────────────────────────────────────────────────────────────────
# Core value objects
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReplayEdge:
    """One permitted directed transition and how its setter list is built."""

    edge: str  # e.g. "activation/baseline-to-moderate"
    kind: str  # activation | rollback | recovery
    order_name: str  # activation | rollback | recovery
    order: tuple[str, ...]
    start_label: str
    target_label: str
    start_state: dict[str, float | bool]
    target_state: dict[str, float | bool]
    complete_bundle: bool

    def summary(self) -> dict[str, Any]:
        return {
            "edge": self.edge,
            "kind": self.kind,
            "order_name": self.order_name,
            "start_label": self.start_label,
            "target_label": self.target_label,
            "complete_bundle": self.complete_bundle,
            "start_state_sha256": prefix_state_sha256(self.start_state),
            "target_state_sha256": prefix_state_sha256(self.target_state),
        }


@dataclass(frozen=True)
class PrefixCase:
    """The COMPLETE 48-field device state after applying the first `index` setters."""

    edge: str
    order_name: str
    index: int
    device_state: dict[str, float | bool]
    applied_fields: tuple[str, ...]
    pending_fields: tuple[str, ...]

    @property
    def case_id(self) -> str:
        return f"{self.edge}/prefix-{self.index:02d}"


@dataclass(frozen=True)
class PrefixVerdict:
    case: PrefixCase
    grid_ok: bool
    registry_clamp_ok: bool
    firmware_clamp_ok: bool
    interlock_safe: str  # safe | unsafe | unproven
    ok: bool
    detail: str = ""
    interlock_evidence_sha256: str | None = None

    def digest_row(self) -> dict[str, Any]:
        """The wall-clock-free prefix identity that feeds ORDER_REVISION."""
        return {
            "applied_fields": list(self.case.applied_fields),
            "edge": self.case.edge,
            "firmware_clamp_ok": self.firmware_clamp_ok,
            "grid_ok": self.grid_ok,
            "index": self.case.index,
            "interlock_evidence_sha256": self.interlock_evidence_sha256,
            "interlock_safe": self.interlock_safe,
            "ok": self.ok,
            "order_name": self.case.order_name,
            "pending_fields": list(self.case.pending_fields),
            "registry_clamp_ok": self.registry_clamp_ok,
            "state_sha256": prefix_state_sha256(self.case.device_state),
        }

    def payload(self) -> dict[str, Any]:
        return {**self.digest_row(), "case_id": self.case.case_id, "detail": self.detail}


@dataclass
class ReplayResult:
    all_pass: bool
    fixed_order_ok: bool
    idempotent_ok: bool
    lands_exact_ok: bool
    verdicts: list[PrefixVerdict]
    order_revision: str | None
    failures: list[str]
    edges: list[dict[str, Any]] = field(default_factory=list)
    interlock: dict[str, Any] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {
            "cases": len(self.verdicts),
            "grid_fail": sum(1 for v in self.verdicts if not v.grid_ok),
            "clamp_fail": sum(1 for v in self.verdicts if not (v.registry_clamp_ok and v.firmware_clamp_ok)),
            "interlock_unsafe": sum(1 for v in self.verdicts if v.interlock_safe == UNSAFE),
            "interlock_unproven": sum(1 for v in self.verdicts if v.interlock_safe == UNPROVEN),
            "ok": sum(1 for v in self.verdicts if v.ok),
        }


# ──────────────────────────────────────────────────────────────────────────────
# Interlock probes
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InterlockOutcome:
    verdict: str  # safe | unsafe | unproven
    detail: str
    evidence_sha256: str | None = None


class InterlockProbe:
    """Adjudicates one prefix state's safety admissibility.

    `covered_fields` is the set of the 48 components the probe can actually
    impose on the firmware under test.  A probe whose coverage is not the full
    48 may return `unsafe` (it observed a real breach) but must never return
    `safe`; partial coverage is not certification.
    """

    name = "abstract"
    covered_fields: frozenset[str] = frozenset()

    def describe(self) -> dict[str, Any]:
        return {
            "covered_field_count": len(self.covered_fields),
            "covered_fields": sorted(self.covered_fields),
            "full_coverage": self.covered_fields == frozenset(CANONICAL_FIELD_ORDER),
            "probe": self.name,
        }

    def verdict(self, case: PrefixCase) -> InterlockOutcome:  # pragma: no cover - abstract
        raise NotImplementedError


class UnprovenInterlock(InterlockProbe):
    """Fail-closed placeholder: every case is `unproven`, never `safe`."""

    name = "unproven"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), "reason": self.reason}

    def verdict(self, case: PrefixCase) -> InterlockOutcome:
        return InterlockOutcome(UNPROVEN, self.reason)


def harness_injection_coverage(source: str, corpus_columns: Sequence[str]) -> dict[str, str]:
    """Map executor component → corpus `sp_*` column the harness actually reads.

    Derived from the harness source rather than a hand-maintained list, so a
    harness that grows an injection column is credited automatically and one
    that loses a column is debited automatically.
    """
    canonical = frozenset(CANONICAL_FIELD_ORDER)
    columns = frozenset(corpus_columns)
    coverage: dict[str, str] = {}
    for match in _SP_ASSIGN.finditer(source):
        member = match.group("member")
        column = match.group("column")
        component = SP_MEMBER_ALIASES.get(member, member)
        if component in canonical and column in columns:
            coverage[component] = column
    return coverage


_COVERAGE_MARKER = "##replay-invariants-coverage-v1"


def parse_coverage_payload(output: str) -> dict[str, Any]:
    """Decode the harness's one self-declared coverage record, fail closed."""
    lines = [line for line in output.splitlines() if line.startswith(f"{_COVERAGE_MARKER} ")]
    if len(lines) != 1:
        raise PrefixReplayError(f"compiled harness emitted {len(lines)} coverage records (expected exactly one)")
    try:
        payload = json.loads(lines[0][len(_COVERAGE_MARKER) + 1 :])
    except json.JSONDecodeError as exc:
        raise PrefixReplayError("compiled harness coverage record is not JSON") from exc
    if not isinstance(payload, dict):
        raise PrefixReplayError("compiled harness coverage record must be an object")
    return payload


def policy_template_injection_coverage(template: str) -> frozenset[str]:
    """Read the compiled binary's 27-field injection ceiling from its template."""
    canonical = frozenset(CANONICAL_FIELD_ORDER)
    rows: dict[str, bool] = {}
    for line in template.splitlines():
        tokens = line.split()
        if not tokens or tokens[0] not in canonical:
            continue
        name = tokens[0]
        if name in rows:
            raise PrefixReplayError(f"compiled policy template duplicates {name}")
        rows[name] = "NOT-IMPOSABLE" not in line
    if frozenset(rows) != canonical:
        missing = sorted(canonical - frozenset(rows))
        extra = sorted(frozenset(rows) - canonical)
        raise PrefixReplayError(f"compiled policy template is not full-48: missing={missing} extra={extra}")
    return frozenset(name for name, injectable in rows.items() if injectable)


class CompiledInvariantInterlock(InterlockProbe):
    """Drives the compiled `replay_invariants` harness over a disturbance corpus.

    For each distinct injectable-value tuple a complete 48-field policy-state
    file is supplied through the harness's out-of-band surface.  The harness
    self-declares which fields it held on every row; that machine record, rather
    than source scanning or exit-code inference, is the coverage authority.
    """

    name = "compiled-replay-invariants"

    def __init__(
        self,
        *,
        binary: Path,
        corpus_path: Path,
        row_count: int,
        coverage: Sequence[str],
        workdir: Path,
        binary_sha256: str,
        source_sha256: str,
        corpus_sha256: str,
    ) -> None:
        self.binary = binary
        self.corpus_path = corpus_path
        self.row_count = row_count
        self.covered_fields = frozenset(coverage)
        self.workdir = workdir
        self.binary_sha256 = binary_sha256
        self.source_sha256 = source_sha256
        self.corpus_sha256 = corpus_sha256
        self._cache: dict[tuple[tuple[str, str], ...], InterlockOutcome] = {}
        self.runs = 0

    def describe(self) -> dict[str, Any]:
        return {
            **super().describe(),
            "binary_sha256": self.binary_sha256,
            "corpus_path": str(self.corpus_path),
            "corpus_rows": self.row_count,
            "corpus_sha256": self.corpus_sha256,
            "harness_runs": self.runs,
            "harness_source_sha256": self.source_sha256,
            "injection_columns": {name: "policy_state" for name in sorted(self.covered_fields)},
            "uncovered_field_count": len(CANONICAL_FIELD_ORDER) - len(self.covered_fields),
            "uncovered_fields": sorted(set(CANONICAL_FIELD_ORDER) - self.covered_fields),
            "uncovered_treatment_fields": sorted(set(TREATMENT_FIELD_ORDER) - self.covered_fields),
        }

    @staticmethod
    def _cell(value: float | bool) -> str:
        # parse_bool() in the harness accepts t/true/1; parse_float()/parse_int()
        # take a plain decimal literal.  Never emit an empty cell — that would
        # silently fall back to the harness default instead of the prefix value.
        if isinstance(value, bool):
            return "1" if value else "0"
        numeric = float(value)
        return str(int(numeric)) if numeric.is_integer() else repr(numeric)

    def _key(self, state: Mapping[str, float | bool]) -> tuple[tuple[str, str], ...]:
        # Cache only byte-identical complete policy files. The remaining 21
        # fields are explicitly unqualified, but reusing evidence from a
        # different full state would sever the per-prefix evidence identity.
        return tuple((name, self._cell(state[name])) for name in CANONICAL_FIELD_ORDER)

    def _coverage_failure(self, payload: Mapping[str, Any], policy_state_sha256: str) -> str | None:
        expected = set(self.covered_fields)
        imposed = payload.get("imposed")
        unimposed = payload.get("unimposed")
        checks = {
            "schema": payload.get("schema") == "verdify-replay-invariants-coverage-v1",
            "field_count": payload.get("field_count") == 48,
            "injectable_count": payload.get("injectable_count") == len(expected) == 27,
            "imposed_count": payload.get("imposed_count") == len(expected),
            "full_coverage": payload.get("full_coverage") is False,
            "full_injectable_coverage": payload.get("full_injectable_coverage") is True,
            "rows": payload.get("rows") == self.row_count,
            "status": payload.get("status") == "ok",
            "policy_state_sha256": payload.get("policy_state_sha256") == policy_state_sha256,
            "imposed_shape": isinstance(imposed, dict) and set(imposed) == expected,
            "unimposed_shape": isinstance(unimposed, dict) and set(unimposed) == set(CANONICAL_FIELD_ORDER) - expected,
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            return f"coverage contract mismatch: {','.join(failed)}"
        assert isinstance(imposed, dict)
        partial = [
            name
            for name, row in imposed.items()
            if not isinstance(row, dict) or row.get("source") != "policy_state" or row.get("rows") != self.row_count
        ]
        if partial:
            return f"coverage was not effective on every row: {','.join(sorted(partial))}"
        return None

    def _run(
        self,
        key: tuple[tuple[str, str], ...],
        state: Mapping[str, float | bool],
    ) -> InterlockOutcome:
        identity = hashlib.sha256(canonical_json(key).encode()).hexdigest()[:16]
        path = self.workdir / f"policy-state-{identity}.txt"
        path.write_text(
            "".join(f"{name} {self._cell(state[name])}\n" for name in CANONICAL_FIELD_ORDER),
            encoding="utf-8",
        )
        policy_state_sha256 = _sha256_bytes(path.read_bytes())
        proc = subprocess.run(  # offline: local binary, local file, no network
            [str(self.binary), str(self.corpus_path), f"--policy-state={path}"],
            capture_output=True,
            text=True,
            timeout=1800,
            env={**os.environ, "LC_ALL": "C"},
        )
        path.unlink(missing_ok=True)
        self.runs += 1
        evidence = _sha256_bytes((proc.stdout + proc.stderr).encode())
        try:
            coverage = parse_coverage_payload(proc.stdout)
        except PrefixReplayError as exc:
            return InterlockOutcome(
                UNPROVEN,
                f"compiled harness coverage unavailable (exit={proc.returncode}): {exc}",
                evidence,
            )
        coverage_failure = self._coverage_failure(coverage, policy_state_sha256)
        if coverage_failure is not None:
            return InterlockOutcome(UNPROVEN, coverage_failure, evidence)
        violations = coverage.get("invariant_violations")
        if isinstance(violations, int) and not isinstance(violations, bool) and violations > 0:
            return InterlockOutcome(
                UNSAFE,
                f"compiled harness found {violations} invariant violations (exit={proc.returncode})",
                evidence,
            )
        if proc.returncode != 0:
            return InterlockOutcome(
                UNPROVEN,
                f"compiled harness failed without a declared invariant breach (exit={proc.returncode})",
                evidence,
            )
        missing = len(CANONICAL_FIELD_ORDER) - len(self.covered_fields)
        return InterlockOutcome(
            UNPROVEN,
            (
                f"no invariant breach over {self.row_count} corpus rows, but compiled policy-state coverage is "
                f"exactly {len(self.covered_fields)}/48; the remaining {missing} fields are explicitly unqualified"
            ),
            evidence,
        )

    def verdict(self, case: PrefixCase) -> InterlockOutcome:
        key = self._key(case.device_state)
        cached = self._cache.get(key)
        if cached is None:
            cached = self._run(key, case.device_state)
            self._cache[key] = cached
        return cached


class ExternalHarnessInterlock(InterlockProbe):
    """Adapter for an operator-supplied full-coverage / HIL runner.

    Protocol: the executable is invoked once per case with a JSON request on
    stdin and must answer with one JSON object on stdout::

        {"schema": "verdify-component-prefix-interlock-verdict-v1",
         "case_id": "<echoed>", "verdict": "safe|unsafe|unproven",
         "covered_fields": [...], "detail": "...", "evidence_sha256": "..."}

    A malformed answer, a mismatched case id, or a "safe" claim that does not
    also declare all 48 components covered is downgraded to `unproven`.
    """

    name = "external-harness"
    REQUEST_SCHEMA = "verdify-component-prefix-interlock-request-v1"
    VERDICT_SCHEMA = "verdify-component-prefix-interlock-verdict-v1"

    def __init__(self, harness: Path, *, timeout: int = 900) -> None:
        self.harness = harness
        self.timeout = timeout
        self.covered_fields = frozenset()
        self.calls = 0
        self.harness_sha256 = _sha256_bytes(harness.read_bytes()) if harness.is_file() else None

    def describe(self) -> dict[str, Any]:
        return {
            **super().describe(),
            "harness": str(self.harness),
            "harness_sha256": self.harness_sha256,
            "calls": self.calls,
        }

    def verdict(self, case: PrefixCase) -> InterlockOutcome:
        request = {
            "case_id": case.case_id,
            "device_state": case.device_state,
            "edge": case.edge,
            "index": case.index,
            "order_name": case.order_name,
            "schema": self.REQUEST_SCHEMA,
        }
        try:
            proc = subprocess.run(  # offline: operator-supplied local executable
                [str(self.harness)],
                input=canonical_json(request),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return InterlockOutcome(UNPROVEN, f"external harness could not be run: {exc}")
        self.calls += 1
        if proc.returncode != 0:
            return InterlockOutcome(UNPROVEN, f"external harness exit={proc.returncode}: {proc.stderr.strip()[:200]}")
        try:
            answer = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return InterlockOutcome(UNPROVEN, "external harness did not emit a JSON verdict")
        if not isinstance(answer, dict) or answer.get("schema") != self.VERDICT_SCHEMA:
            return InterlockOutcome(UNPROVEN, "external harness verdict has the wrong schema")
        if answer.get("case_id") != case.case_id:
            return InterlockOutcome(UNPROVEN, "external harness answered a different case id")
        covered = answer.get("covered_fields")
        if isinstance(covered, list) and all(isinstance(item, str) for item in covered):
            self.covered_fields = self.covered_fields | frozenset(covered)
        detail = str(answer.get("detail", ""))[:400]
        claimed = answer.get("verdict")
        raw_evidence = answer.get("evidence_sha256")
        evidence_sha256 = (
            raw_evidence
            if isinstance(raw_evidence, str) and re.fullmatch(r"[0-9a-f]{64}", raw_evidence) is not None
            else None
        )
        if claimed == UNSAFE:
            return InterlockOutcome(
                UNSAFE,
                detail or "external harness reported an unsafe prefix state",
                evidence_sha256,
            )
        if claimed != SAFE:
            return InterlockOutcome(UNPROVEN, detail or f"external harness verdict {claimed!r}", evidence_sha256)
        if not isinstance(covered, list) or frozenset(covered) != frozenset(CANONICAL_FIELD_ORDER):
            return InterlockOutcome(UNPROVEN, "external harness claimed safe without declaring all 48 components")
        if evidence_sha256 is None:
            return InterlockOutcome(UNPROVEN, "external harness claimed safe without a lowercase sha256 evidence id")
        return InterlockOutcome(SAFE, detail or "external harness reported a safe prefix state", evidence_sha256)


def build_compiled_interlock(
    *,
    corpus: Path,
    workdir: Path,
    compiler: str,
    max_rows: int = 0,
) -> InterlockProbe:
    """Compile and prime the `replay_invariants` harness, or explain why not."""
    if not INVARIANTS_SOURCE.is_file():
        return UnprovenInterlock(f"harness source missing: {INVARIANTS_SOURCE}")
    if not corpus.is_file():
        return UnprovenInterlock(f"disturbance corpus missing: {corpus}")
    resolved = shutil.which(compiler)
    if resolved is None:
        return UnprovenInterlock(f"no host C++ compiler on PATH ({compiler}) — cannot build the compiled harness")

    binary = workdir / "replay_invariants"
    build = subprocess.run(  # offline: local toolchain over committed sources
        [resolved, "-std=c++17", "-O2", "-I", str(FIRMWARE_LIB), "-o", str(binary), str(INVARIANTS_SOURCE)],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if build.returncode != 0 or not binary.is_file():
        return UnprovenInterlock(f"compiled harness build failed: {build.stderr.strip().splitlines()[-1:] or ''}")

    template = subprocess.run(
        [str(binary), "--print-policy-template"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if template.returncode != 0:
        return UnprovenInterlock(f"compiled harness policy template failed: exit={template.returncode}")
    try:
        coverage = policy_template_injection_coverage(template.stdout)
    except PrefixReplayError as exc:
        return UnprovenInterlock(str(exc))
    if len(coverage) != 27:
        return UnprovenInterlock(
            f"compiled harness injection ceiling changed: expected honest 27/48, observed {len(coverage)}/48"
        )

    opener = gzip.open if corpus.suffix == ".gz" else open
    with opener(corpus, "rt", encoding="utf-8", newline="") as handle:  # type: ignore[operator]
        lines = handle.read().splitlines()
    if not lines:
        return UnprovenInterlock(f"disturbance corpus is empty: {corpus}")
    header = lines[0].split("\t")
    rows = lines[1:]
    if max_rows > 0:
        rows = rows[:max_rows]
    if not rows:
        return UnprovenInterlock(f"disturbance corpus has no data rows: {corpus}")

    source = INVARIANTS_SOURCE.read_text(encoding="utf-8")
    execution_corpus = workdir / "replay-corpus.tsv"
    execution_corpus.write_text("\n".join(["\t".join(header), *rows]) + "\n", encoding="utf-8")
    return CompiledInvariantInterlock(
        binary=binary,
        corpus_path=execution_corpus,
        row_count=len(rows),
        coverage=sorted(coverage),
        workdir=workdir,
        binary_sha256=_sha256_bytes(binary.read_bytes()),
        source_sha256=_sha256_bytes(source.encode()),
        corpus_sha256=_sha256_bytes(execution_corpus.read_bytes()),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Prefix enumeration + replay (pure logic; no I/O below this line)
# ──────────────────────────────────────────────────────────────────────────────


def _command_fields(
    start: Mapping[str, float | bool],
    target: Mapping[str, float | bool],
    *,
    order: Sequence[str],
    complete_bundle: bool,
) -> tuple[ComponentChange, ...]:
    """The exact setter list, built ONLY by the source-locked contracts."""
    if complete_bundle:
        return fixed_order_complete_bundle(target, order=order)
    return fixed_order_differences(start, target, order=order)


def enumerate_prefixes(
    start: Mapping[str, float | bool],
    target: Mapping[str, float | bool],
    *,
    order: Sequence[str],
    order_name: str,
    edge: str = "",
    complete_bundle: bool = False,
) -> list[PrefixCase]:
    """Every state reachable by truncating the setter list to length 0..N.

    Each case carries the COMPLETE 48-field state: components the setter list
    has not reached yet keep the start's value, including a start value that is
    itself off-grid (a real compiled default may be, and it must be preserved
    rather than silently repaired — the grid verdict is then the honest one).
    """
    changes = _command_fields(start, target, order=order, complete_bundle=complete_bundle)
    fields = tuple(change.field_name for change in changes)
    expected = tuple(order) if complete_bundle else tuple(f for f in order if start[f] != target[f])
    if fields != expected:
        raise PrefixReplayError(f"{edge or order_name}: setter list is not the fixed source order")

    state: dict[str, float | bool] = dict(start)
    cases: list[PrefixCase] = []
    for index in range(len(fields) + 1):
        if index:
            change = changes[index - 1]
            state[change.field_name] = change.value
        cases.append(
            PrefixCase(
                edge=edge or f"{order_name}/prefix",
                order_name=order_name,
                index=index,
                device_state=dict(state),
                applied_fields=fields[:index],
                pending_fields=fields[index:],
            )
        )
    return cases


def _grade(case: PrefixCase, interlock: InterlockOutcome) -> PrefixVerdict:
    details: list[str] = []
    try:
        normalize_complete_state(case.device_state)
        grid_ok = True
    except ComponentContractError as exc:
        grid_ok = False
        offender = exc.detail.split("=")[0] if "=" in exc.detail else exc.detail
        inherited = offender not in case.applied_fields
        details.append(
            f"grid {exc.code}: {exc.detail} "
            f"({'inherited from the start state' if inherited else 'COMMANDED BY THE SETTER LIST'})"
        )

    registry_bad = _clamp_violations(case.device_state, registry_clamp_ok)
    firmware_bad = _clamp_violations(case.device_state, firmware_clamp_ok)
    if registry_bad:
        details.append("registry clamp: " + ", ".join(registry_bad[:4]))
    if firmware_bad:
        details.append("firmware clamp: " + ", ".join(firmware_bad[:4]))
    if interlock.verdict != SAFE:
        details.append(f"interlock {interlock.verdict}: {interlock.detail}")

    ok = grid_ok and not registry_bad and not firmware_bad and interlock.verdict == SAFE
    return PrefixVerdict(
        case=case,
        grid_ok=grid_ok,
        registry_clamp_ok=not registry_bad,
        firmware_clamp_ok=not firmware_bad,
        interlock_safe=interlock.verdict,
        ok=ok,
        detail="; ".join(details),
        interlock_evidence_sha256=interlock.evidence_sha256,
    )


def _check_idempotency(edge: ReplayEdge, cases: Sequence[PrefixCase], failures: list[str]) -> bool:
    """Re-deriving work from a confirmed prefix must yield only the pending suffix.

    This is the property that makes a retry safe: an executor that reconnects
    after confirming prefix i and re-derives its work list must never re-issue
    a component it already confirmed, and must still be asked for every
    component it has not.
    """
    ok = True
    for case in cases:
        try:
            replay_now = fixed_order_differences(case.device_state, edge.target_state, order=edge.order)
        except ComponentContractError as exc:
            failures.append(f"{case.case_id}: idempotency undecidable ({exc.code}: {exc.detail})")
            ok = False
            continue
        redone = [c.field_name for c in replay_now if c.field_name in case.applied_fields]
        if redone:
            failures.append(f"{case.case_id}: confirmed components would be re-issued: {','.join(redone)}")
            ok = False
        expected_pending = [f for f in edge.order if case.device_state[f] != edge.target_state[f]]
        if [c.field_name for c in replay_now] != expected_pending:
            failures.append(f"{case.case_id}: re-derived work is not the pending suffix")
            ok = False
    return ok


def replay(
    edges: Sequence[ReplayEdge],
    *,
    interlock: InterlockProbe | None = None,
    revision_prefix: str = ORDER_REVISION_PREFIX,
) -> ReplayResult:
    """Adjudicate every prefix of every edge and derive the qualified revision."""
    probe = interlock or UnprovenInterlock("no interlock probe supplied")
    failures: list[str] = []
    verdicts: list[PrefixVerdict] = []
    edge_rows: list[dict[str, Any]] = []
    fixed_order_ok = True
    idempotent_ok = True
    lands_exact_ok = True

    if not edges:
        failures.append("no edges to replay — an empty qualification proves nothing")

    # Check 2, part one: the order constants themselves must still be the
    # source tuples this tool claims to have qualified.
    if ACTIVATION_ORDER != TREATMENT_FIELD_ORDER or ROLLBACK_ORDER != TREATMENT_FIELD_ORDER:
        failures.append("activation/rollback order is no longer TREATMENT_FIELD_ORDER")
        fixed_order_ok = False
    if len(RECOVERY_ORDER) != len(CANONICAL_FIELD_ORDER) or set(RECOVERY_ORDER) != set(CANONICAL_FIELD_ORDER):
        failures.append("recovery order is no longer an exact permutation of CANONICAL_FIELD_ORDER")
        fixed_order_ok = False

    for edge in edges:
        # Check 2, part two: an edge may only be replayed against the exact
        # source tuple for its order.  A caller that hands in a permuted or
        # ad-hoc order is building evidence for an order the executor will
        # never use.
        expected_order = SOURCE_ORDERS.get(edge.order_name)
        if expected_order is None or tuple(edge.order) != expected_order:
            failures.append(f"{edge.edge}: order is not the source {edge.order_name!r} tuple")
            fixed_order_ok = False
        try:
            cases = enumerate_prefixes(
                edge.start_state,
                edge.target_state,
                order=edge.order,
                order_name=edge.order_name,
                edge=edge.edge,
                complete_bundle=edge.complete_bundle,
            )
        except (ComponentContractError, PrefixReplayError) as exc:
            failures.append(f"{edge.edge}: fixed-order build refused ({exc})")
            fixed_order_ok = False
            edge_rows.append({**edge.summary(), "prefix_count": 0, "command_fields": []})
            continue

        command_fields = list(cases[-1].applied_fields)
        edge_rows.append({**edge.summary(), "prefix_count": len(cases), "command_fields": command_fields})

        for case in cases:
            verdicts.append(_grade(case, probe.verdict(case)))

        # Check 6: the last prefix state is exactly the normalized target.
        try:
            landed = cases[-1].device_state == normalize_complete_state(edge.target_state)
        except ComponentContractError as exc:
            failures.append(f"{edge.edge}: target is not grid-normalizable ({exc.code}: {exc.detail})")
            landed = False
        if not landed:
            failures.append(f"{edge.edge}: final prefix state is not the normalized target")
            lands_exact_ok = False

        # Check 5.
        if not _check_idempotency(edge, cases, failures):
            idempotent_ok = False

    failures.extend(f"{v.case.case_id}: {v.detail}" for v in verdicts if not v.ok)

    all_pass = bool(
        edges
        and verdicts
        and fixed_order_ok
        and idempotent_ok
        and lands_exact_ok
        and all(v.ok for v in verdicts)
        and not failures
    )
    result = ReplayResult(
        all_pass=all_pass,
        fixed_order_ok=fixed_order_ok,
        idempotent_ok=idempotent_ok,
        lands_exact_ok=lands_exact_ok,
        verdicts=verdicts,
        order_revision=None,
        failures=failures,
        edges=edge_rows,
        interlock=probe.describe(),
    )
    if all_pass:
        result.order_revision = derive_order_revision(result, revision_prefix=revision_prefix)
    return result


_REVISION_INTERLOCK_KEYS = (
    "probe",
    "covered_field_count",
    "covered_fields",
    "full_coverage",
    "binary_sha256",
    "harness_source_sha256",
    "harness_sha256",
    "corpus_sha256",
    "corpus_rows",
    "injection_columns",
    "uncovered_field_count",
    "uncovered_fields",
    "uncovered_treatment_fields",
)


def revision_interlock_payload(description: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministic compiled/HIL identity; excludes paths, clocks and run counts."""
    return {key: description[key] for key in _REVISION_INTERLOCK_KEYS if description.get(key) is not None}


def revision_digest_payload(result: ReplayResult) -> dict[str, Any]:
    """The exact prefix states, orders and firmware/HIL evidence qualified."""
    return {
        "edges": result.edges,
        "interlock": revision_interlock_payload(result.interlock),
        "orders": {
            "activation": list(ACTIVATION_ORDER),
            "recovery": list(RECOVERY_ORDER),
            "rollback": list(ROLLBACK_ORDER),
        },
        "verdicts": [v.digest_row() for v in result.verdicts],
    }


def derive_order_revision(result: ReplayResult, *, revision_prefix: str = ORDER_REVISION_PREFIX) -> str:
    """`prefix-replay-v1:sha256:<H2>` — only ever called on an all-pass result.

    H2 binds the exact state at every prefix, fixed orders, boolean verdicts,
    per-case evidence digests, and the deterministic compiled/HIL identity.
    Filesystem paths, run counters, free text and wall clocks are excluded so
    the same evidence re-derives identically without allowing changed firmware,
    corpus, profiles or prefix values to reuse a stale revision.
    """
    if not result.all_pass:
        raise PrefixReplayError("order_revision is only derivable from an all-pass replay")
    digest = hashlib.sha256(canonical_json(revision_digest_payload(result)).encode()).hexdigest()
    revision = f"{revision_prefix}:sha256:{digest}"
    if _QUALIFIED_ORDER_REVISION.fullmatch(revision) is None:
        raise PrefixReplayError(f"derived revision does not satisfy the executor contract: {revision}")
    return revision


# ──────────────────────────────────────────────────────────────────────────────
# Edge construction from committed artifacts
# ──────────────────────────────────────────────────────────────────────────────


def load_profiles(path: Path) -> dict[str, dict[str, float | bool]]:
    """Decode the three switchback profiles from their canonical wire bytes."""
    artifact = json.loads(path.read_text(encoding="utf-8"))
    rows = artifact.get("profiles")
    if not isinstance(rows, Mapping) or set(rows) != set(PROFILE_NAMES):
        raise PrefixReplayError(f"{path} must carry exactly baseline/moderate/aggressive profiles")
    profiles: dict[str, dict[str, float | bool]] = {}
    for name in PROFILE_NAMES:
        row = rows[name]
        if not isinstance(row, Mapping) or not isinstance(row.get("wire_hex"), str):
            raise PrefixReplayError(f"profile {name} has no wire_hex")
        try:
            values = decode_policy_vector(bytes.fromhex(str(row["wire_hex"])))
            profiles[name] = normalize_complete_state(values)
        except (ValueError, ComponentContractError) as exc:
            raise PrefixReplayError(f"profile {name} is not exact-grid canonical: {exc}") from exc
    return profiles


def _prep_module():
    """Import the sibling preparation tool (shared compiled-default parser)."""
    path = Path(__file__).resolve().parent / "prepare_component_prefix_replay.py"
    spec = importlib.util.spec_from_file_location("prepare_component_prefix_replay", path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_start_state(path: Path, label: str) -> dict[str, float | bool]:
    """Read one complete 48-field start state (observed / reboot / reset / drift).

    Accepts either the bare 48-field object or a
    `verdify-component-current-state-v1` artifact.  Values are validated as
    finite and inside their source-declared wire bounds but NOT forced onto the
    entity grid — an off-grid start is exactly what the recovery edge exists to
    repair, and rounding it here would hide the finding.
    """
    prep = _prep_module()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise PrefixReplayError(f"start state {label} must be a JSON object")
    values = raw.get("values") if raw.get("schema") == prep.CURRENT_STATE_SCHEMA else raw
    try:
        return prep._finite_raw_state(values, f"start state {label}")
    except prep.PrefixPreparationError as exc:
        raise PrefixReplayError(str(exc)) from exc


def load_compiled_defaults(generated_main: Path, consumer_manifest: Path) -> dict[str, float | bool]:
    """Extract the 48 compiled ESPHome constructor defaults (no YAML inference)."""
    prep = _prep_module()
    try:
        return prep.extract_compiled_defaults(
            generated_main.read_bytes(),
            json.loads(consumer_manifest.read_text(encoding="utf-8")),
        )
    except (prep.PrefixPreparationError, OSError, json.JSONDecodeError) as exc:
        raise PrefixReplayError(f"compiled defaults unavailable: {exc}") from exc


def build_edges(
    profiles: Mapping[str, dict[str, float | bool]],
    recovery_starts: Mapping[str, dict[str, float | bool]],
) -> list[ReplayEdge]:
    """The four permitted treatment edges plus one full-48 recovery per start."""
    edges: list[ReplayEdge] = []
    for start_label, target_label in TREATMENT_EDGES:
        kind = "activation" if start_label == "baseline" else "rollback"
        edges.append(
            ReplayEdge(
                edge=f"{kind}/{start_label}-to-{target_label}",
                kind=kind,
                order_name=kind,
                order=ACTIVATION_ORDER if kind == "activation" else ROLLBACK_ORDER,
                start_label=start_label,
                target_label=target_label,
                start_state=dict(profiles[start_label]),
                target_state=dict(profiles[target_label]),
                complete_bundle=False,
            )
        )
    for start_label in sorted(recovery_starts):
        edges.append(
            ReplayEdge(
                edge=f"recovery/{start_label}-to-baseline",
                kind="recovery",
                order_name="recovery",
                order=RECOVERY_ORDER,
                start_label=start_label,
                target_label="baseline",
                start_state=dict(recovery_starts[start_label]),
                target_state=dict(profiles["baseline"]),
                complete_bundle=True,
            )
        )
    return edges


# ──────────────────────────────────────────────────────────────────────────────
# CLI reporting (PASS/FAIL/WARN, same shape as scripts/experiment-verify.py)
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Check:
    name: str
    status: str  # PASS | FAIL | WARN
    detail: str = ""

    def payload(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def _check(name: str, condition: bool, detail: str = "") -> Check:
    return Check(name, "PASS" if condition else "FAIL", detail)


def build_checks(result: ReplayResult, profiles: Mapping[str, dict[str, float | bool]]) -> list[Check]:
    counts = result.counts()
    checks: list[Check] = []

    for edge in result.edges:
        checks.append(
            Check(
                f"enumerate/{edge['edge']}",
                "PASS" if edge["prefix_count"] else "FAIL",
                f"{edge['prefix_count']} prefixes over {len(edge['command_fields'])} setters "
                f"({edge['order_name']} order)",
            )
        )

    # Routine-target contract: a treatment profile may differ from baseline only
    # on the 11-field allowlist.  Reuses the executor's own validator.
    drift: list[str] = []
    for name in ("moderate", "aggressive"):
        try:
            validate_routine_target(profiles["baseline"], profiles[name])
        except ComponentContractError as exc:
            drift.append(f"{name}: {exc.code} {exc.detail}")
    checks.append(
        _check(
            "treatment/common-fields-frozen",
            not drift,
            "; ".join(drift)
            or f"moderate+aggressive differ from baseline only on the {len(TREATMENT_FIELD_ORDER)} "
            f"treatment fields ({len(COMMON_FIELDS)} common fields identical)",
        )
    )

    checks.append(
        _check("fixed-order/source-tuples", result.fixed_order_ok, "change lists built by fixed_order_* contracts")
    )
    checks.append(
        _check(
            "grid/every-prefix-state",
            counts["grid_fail"] == 0,
            f"{counts['grid_fail']}/{counts['cases']} prefix states rejected by normalize_complete_state",
        )
    )
    checks.append(
        _check(
            "clamps/registry-and-firmware",
            counts["clamp_fail"] == 0,
            f"{counts['clamp_fail']}/{counts['cases']} prefix states outside registry or firmware clamps",
        )
    )
    interlock_detail = (
        f"{counts['interlock_unsafe']} unsafe, {counts['interlock_unproven']} unproven of {counts['cases']} "
        f"(probe={result.interlock.get('probe')}, coverage={result.interlock.get('covered_field_count')}/48)"
    )
    if counts["interlock_unsafe"]:
        checks.append(Check("interlock/safety-admissible", "FAIL", interlock_detail))
    elif counts["interlock_unproven"]:
        checks.append(
            Check("interlock/safety-admissible", "FAIL", interlock_detail + " — unproven blocks the revision")
        )
    else:
        checks.append(Check("interlock/safety-admissible", "PASS", interlock_detail))
    checks.append(
        _check(
            "idempotency/no-reissue",
            result.idempotent_ok,
            "confirmed prefixes re-derive to the pending suffix only"
            if result.idempotent_ok
            else "at least one prefix would re-issue a confirmed component, or is undecidable",
        )
    )
    checks.append(_check("lands-exact/final-state", result.lands_exact_ok, "every edge lands on the normalized target"))
    return checks


def distinct_failures(failures: Sequence[str]) -> dict[str, tuple[int, str]]:
    """Collapse per-case failure lines to {cause: (count, first example)}.

    The cause key is the message with its leading `case_id: ` stripped and
    digits masked, so `prefix-00`/`prefix-01` variants of one root cause group.
    """
    grouped: dict[str, tuple[int, str]] = {}
    for failure in failures:
        _, _, message = failure.partition(": ")
        cause = re.sub(r"\d+", "#", message or failure)
        count, exemplar = grouped.get(cause, (0, failure))
        grouped[cause] = (count + 1, exemplar)
    return grouped


def report(checks: Sequence[Check], result: ReplayResult, failures_shown: int = 12) -> int:
    for check in checks:
        line = f"{check.status} — {check.name}"
        if check.detail:
            line += f": {check.detail}"
        print(line)
    n_fail = sum(1 for c in checks if c.status == "FAIL")
    n_warn = sum(1 for c in checks if c.status == "WARN")
    print(f"SUMMARY: {len(checks) - n_fail - n_warn} pass, {n_warn} warn, {n_fail} fail")
    # An 81-case run repeats the same root cause many times; print one exemplar
    # per distinct cause with its multiplicity so the real blockers are legible.
    for reason, (count, exemplar) in list(distinct_failures(result.failures).items())[:failures_shown]:
        del reason
        print(f"    ! [x{count}] {exemplar}")
    remaining = len(distinct_failures(result.failures)) - failures_shown
    if remaining > 0:
        print(f"    ! … {remaining} more distinct causes ({len(result.failures)} failure lines total)")
    # The ORDER_REVISION binds the stable hashes/coverage in this block (and the
    # exact per-prefix state/evidence ids).  Print the full human-facing block as
    # well; paths and run counters remain useful diagnostics even though they
    # are intentionally absent from the deterministic revision preimage.
    print(
        "interlock_evidence: "
        + canonical_json(
            {
                key: result.interlock.get(key)
                for key in (
                    "probe",
                    "covered_field_count",
                    "full_coverage",
                    "binary_sha256",
                    "corpus_sha256",
                    "harness_source_sha256",
                    "harness_runs",
                    "harness",
                    "reason",
                )
                if result.interlock.get(key) is not None
            }
        )
    )
    if result.order_revision:
        print(f"order_revision={result.order_revision}")
    else:
        print("order_revision=NOT EMITTED (replay did not pass every check)")
    print(
        f"OVERALL {'PASS' if result.all_pass else 'FAIL'} ({result.counts()['ok']}/{result.counts()['cases']} "
        "prefix cases admissible)"
    )
    return 0 if result.all_pass else 1


def _iter_start_args(pairs: Iterable[tuple[str, Path]]) -> dict[str, Path]:
    seen: dict[str, Path] = {}
    for label, path in pairs:
        if label in seen:
            raise PrefixReplayError(f"duplicate start label {label}")
        seen[label] = path
    return seen


def _start_arg(value: str) -> tuple[str, Path]:
    label, separator, raw = value.partition("=")
    if not separator or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", label):
        raise argparse.ArgumentTypeError("start state must be LABEL=PATH with a bounded lowercase label")
    return label, Path(raw)


def _make_interlock(args: argparse.Namespace, workdir: Path) -> InterlockProbe:
    if args.interlock == "none":
        return UnprovenInterlock("interlock explicitly disabled (--interlock none); a revision cannot be earned here")
    if args.interlock == "external":
        if not args.interlock_harness:
            return UnprovenInterlock("--interlock external requires --interlock-harness PATH")
        return ExternalHarnessInterlock(Path(args.interlock_harness))
    return build_compiled_interlock(
        corpus=Path(args.interlock_corpus),
        workdir=workdir,
        compiler=args.interlock_cxx,
        max_rows=args.interlock_corpus_rows,
    )


def _run_replay(args: argparse.Namespace) -> int:
    profiles = load_profiles(Path(args.profiles))
    starts: dict[str, dict[str, float | bool]] = {}
    if args.compiled_defaults:
        starts["compiled-defaults"] = load_compiled_defaults(Path(args.compiled_defaults), Path(args.consumer_manifest))
    for label, path in _iter_start_args(args.start).items():
        starts[label] = load_start_state(path, label)

    edges = build_edges(profiles, starts)
    with tempfile.TemporaryDirectory(prefix="component-prefix-replay-") as tmp:
        workdir = Path(tmp)
        probe = _make_interlock(args, workdir)
        result = replay(edges, interlock=probe)

    if not starts:
        result.failures.insert(0, "no recovery start supplied (--compiled-defaults / --start) — recovery unqualified")
        result.all_pass = False
        result.order_revision = None

    checks = build_checks(result, profiles)
    exit_code = report(checks, result)

    if args.json_out:
        payload: dict[str, Any] = {
            "all_pass": result.all_pass,
            "checks": [c.payload() for c in checks],
            "computed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "counts": result.counts(),
            "edges": result.edges,
            "failures": result.failures,
            "fixed_order_ok": result.fixed_order_ok,
            "idempotent_ok": result.idempotent_ok,
            "interlock": result.interlock,
            "lands_exact_ok": result.lands_exact_ok,
            "order_revision": result.order_revision,
            "orders": revision_digest_payload(result)["orders"],
            "schema": SCHEMA_VERSION,
            "verdicts": [v.payload() for v in result.verdicts],
        }
        payload["result_sha256"] = result_sha256(payload)
        Path(args.json_out).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {args.json_out} (result_sha256={payload['result_sha256']})")
    return exit_code


def _run_probe(args: argparse.Namespace) -> int:
    """Report exactly what the compiled interlock harness can and cannot impose."""
    with tempfile.TemporaryDirectory(prefix="component-prefix-probe-") as tmp:
        probe = build_compiled_interlock(
            corpus=Path(args.interlock_corpus),
            workdir=Path(tmp),
            compiler=args.interlock_cxx,
            max_rows=args.interlock_corpus_rows,
        )
        description = probe.describe()
    covered = frozenset(description.get("covered_fields", ()))
    full = description.get("full_coverage", False)
    checks = [
        Check(
            "harness/buildable",
            "PASS" if probe.name != "unproven" else "FAIL",
            description.get("reason", f"probe={probe.name}"),
        ),
        Check(
            "harness/full-48-injection",
            "PASS" if full else "FAIL",
            f"{len(covered)}/48 components injectable; treatment fields not injectable: "
            f"{', '.join(sorted(set(TREATMENT_FIELD_ORDER) - covered)) or 'none'}",
        ),
    ]
    for check in checks:
        print(f"{check.status} — {check.name}: {check.detail}")
    print(json.dumps(description, indent=2, sort_keys=True))
    return 0 if full else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _common(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--interlock",
            choices=("auto", "external", "none"),
            default="auto",
            help="auto = drive the compiled replay_invariants harness (default)",
        )
        target.add_argument("--interlock-harness", help="executable adapter for --interlock external")
        target.add_argument("--interlock-corpus", default=str(DEFAULT_CORPUS))
        target.add_argument(
            "--interlock-corpus-rows",
            type=int,
            default=0,
            help="0 = the whole corpus; a positive N truncates it (faster, weaker)",
        )
        target.add_argument("--interlock-cxx", default=os.environ.get("CXX", "g++"))

    run = sub.add_parser("replay", help="adjudicate every prefix of every permitted edge")
    run.add_argument("--profiles", default=str(DEFAULT_PROFILES))
    run.add_argument("--compiled-defaults", help="ESPHome-generated main.cpp for the candidate build")
    run.add_argument("--consumer-manifest", default=str(DEFAULT_CONSUMER_MANIFEST))
    run.add_argument(
        "--start",
        action="append",
        default=[],
        type=_start_arg,
        metavar="LABEL=PATH",
        help="complete 48-field observed/reboot/reset/common-drift start state",
    )
    run.add_argument("--json", dest="json_out", help="write the machine-readable verdict payload here")
    _common(run)

    probe = sub.add_parser("probe-harness", help="report the compiled harness's injection coverage")
    _common(probe)

    args = parser.parse_args(argv)
    try:
        if args.command == "replay":
            return _run_replay(args)
        return _run_probe(args)
    except PrefixReplayError as exc:
        parser.exit(2, f"prefix replay refused: {exc}\n")
    return 2


# Import-time fail-closed assertion: this tool must mint only strings the
# executor's own regex accepts.  A drift in either regex fails startup.
if _QUALIFIED_ORDER_REVISION.pattern != r"^prefix-replay-v[1-9][0-9]*:sha256:[0-9a-f]{64}$":
    raise RuntimeError("order-revision contract drift")


if __name__ == "__main__":
    raise SystemExit(main())
