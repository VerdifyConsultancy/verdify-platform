"""Offline compiled-prefix replay is exhaustive, order-locked and fail-closed.

Every test here exercises the pure-logic core of
``scripts/component_prefix_replay.py``: no compiler, no corpus, no device, no
database.  The compiled interlock harness is stubbed so the qualification
arithmetic (including the ORDER_REVISION derivation) can be proven without a
firmware toolchain; ``test_default_compiled_probe_cannot_certify_the_full_grid``
pins the real coverage arithmetic instead.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from verdify_schemas.component_executor import (
    ACTIVATION_ORDER,
    CANONICAL_FIELD_ORDER,
    COMMON_FIELDS,
    RECOVERY_ORDER,
    ROLLBACK_ORDER,
    TREATMENT_FIELD_ORDER,
    ComponentContractError,
    normalize_complete_state,
)
from verdify_schemas.tunable_registry import REGISTRY

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "component_prefix_replay.py"
SPEC = importlib.util.spec_from_file_location("component_prefix_replay", SCRIPT)
assert SPEC and SPEC.loader
tool = importlib.util.module_from_spec(SPEC)
# Register before exec: the module defines dataclasses, and @dataclass resolves
# annotations through sys.modules[cls.__module__].
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)

BASELINE_SPEC = importlib.util.spec_from_file_location(
    "planner_efficacy_baseline", ROOT / "research/planner-efficacy/baseline/baseline.py"
)
assert BASELINE_SPEC and BASELINE_SPEC.loader
baseline_mod = importlib.util.module_from_spec(BASELINE_SPEC)
sys.modules[BASELINE_SPEC.name] = baseline_mod
BASELINE_SPEC.loader.exec_module(baseline_mod)

COMPILED_DEFAULT_FIXTURE = (
    ROOT / "tests/fixtures/component_prefix_replay/esphome-2026.6.5-compiled-default-constructors.cpp"
)
CONSUMER_MANIFEST = ROOT / "firmware/policy_consumer_manifest.json"

# Expected prefix counts for the committed switchback-v2 profiles: N differing
# treatment components produce N+1 prefixes (lengths 0..N); a full-48 recovery
# bundle is unconditional and therefore always 49.
EXPECTED_TREATMENT_PREFIXES = {
    "activation/baseline-to-moderate": 7,
    "rollback/moderate-to-baseline": 7,
    "activation/baseline-to-aggressive": 9,
    "rollback/aggressive-to-baseline": 9,
}
RECOVERY_PREFIXES = 49


# ── fixtures / doubles ───────────────────────────────────────────────────────


class StubInterlock(tool.InterlockProbe):
    """Full-coverage safety probe double — the only way to reach an all-pass run."""

    name = "stub-full-coverage"

    def __init__(self, *, verdict: str = tool.SAFE, unsafe_cases: frozenset[str] = frozenset()) -> None:
        self.covered_fields = frozenset(CANONICAL_FIELD_ORDER)
        self._verdict = verdict
        self._unsafe_cases = unsafe_cases
        self.seen: list[str] = []

    def verdict(self, case: tool.PrefixCase) -> tool.InterlockOutcome:
        self.seen.append(case.case_id)
        if case.case_id in self._unsafe_cases:
            return tool.InterlockOutcome(tool.UNSAFE, "stubbed breach")
        return tool.InterlockOutcome(self._verdict, "stub")


@pytest.fixture(scope="module")
def profiles() -> dict[str, dict[str, float | bool]]:
    return tool.load_profiles(tool.DEFAULT_PROFILES)


@pytest.fixture(scope="module")
def compiled_defaults() -> dict[str, float | bool]:
    return tool.load_compiled_defaults(COMPILED_DEFAULT_FIXTURE, CONSUMER_MANIFEST)


def treatment_edges(profiles: dict[str, dict[str, float | bool]]) -> list[tool.ReplayEdge]:
    return [edge for edge in tool.build_edges(profiles, {}) if edge.kind != "recovery"]


def passing_result(profiles: dict[str, dict[str, float | bool]], **kwargs) -> tool.ReplayResult:
    return tool.replay(treatment_edges(profiles), interlock=StubInterlock(**kwargs))


# ── enumeration ──────────────────────────────────────────────────────────────


def test_every_permitted_edge_enumerates_the_expected_prefix_count(profiles) -> None:
    for edge in treatment_edges(profiles):
        cases = tool.enumerate_prefixes(
            edge.start_state,
            edge.target_state,
            order=edge.order,
            order_name=edge.order_name,
            edge=edge.edge,
        )
        assert len(cases) == EXPECTED_TREATMENT_PREFIXES[edge.edge]
        assert [c.index for c in cases] == list(range(len(cases)))
        assert cases[0].applied_fields == ()
        assert cases[-1].pending_fields == ()
        # Case ids are spelled exactly as scripts/prepare_component_prefix_replay.py
        # spells them so the preparation packet and these verdicts join.
        assert cases[3].case_id == f"{edge.edge}/prefix-03"


def test_recovery_edge_is_the_unconditional_full_48_bundle(profiles, compiled_defaults) -> None:
    cases = tool.enumerate_prefixes(
        compiled_defaults,
        profiles["baseline"],
        order=RECOVERY_ORDER,
        order_name="recovery",
        edge="recovery/compiled-defaults-to-baseline",
        complete_bundle=True,
    )
    assert len(cases) == RECOVERY_PREFIXES
    # Unconditional: every one of the 48 components is commanded even where the
    # start already matches the target.
    assert cases[-1].applied_fields == RECOVERY_ORDER
    already_equal = [f for f in CANONICAL_FIELD_ORDER if compiled_defaults[f] == profiles["baseline"][f]]
    assert already_equal, "fixture must contain at least one already-correct component"
    assert set(already_equal) <= set(cases[-1].applied_fields)


def test_every_prefix_carries_the_complete_48_field_state(profiles) -> None:
    for edge in treatment_edges(profiles):
        cases = tool.enumerate_prefixes(
            edge.start_state, edge.target_state, order=edge.order, order_name=edge.order_name, edge=edge.edge
        )
        for case in cases:
            assert set(case.device_state) == set(CANONICAL_FIELD_ORDER)
            assert len(case.device_state) == 48
            # Untouched components keep the START value, not the target's.
            for name in COMMON_FIELDS:
                assert case.device_state[name] == edge.start_state[name]
            for name in case.pending_fields:
                assert case.device_state[name] == edge.start_state[name]
            for name in case.applied_fields:
                assert case.device_state[name] == edge.target_state[name]


def test_prefix_states_are_snapshots_not_aliases(profiles) -> None:
    edge = treatment_edges(profiles)[0]
    cases = tool.enumerate_prefixes(
        edge.start_state, edge.target_state, order=edge.order, order_name=edge.order_name, edge=edge.edge
    )
    cases[0].device_state["fog_escalation_kpa"] = -1.0
    assert cases[-1].device_state["fog_escalation_kpa"] == edge.target_state["fog_escalation_kpa"]


# ── order enforcement ────────────────────────────────────────────────────────


def test_source_order_constants_are_the_ones_this_tool_qualifies() -> None:
    assert ACTIVATION_ORDER == TREATMENT_FIELD_ORDER
    assert ROLLBACK_ORDER == TREATMENT_FIELD_ORDER
    assert len(RECOVERY_ORDER) == len(CANONICAL_FIELD_ORDER)
    assert set(RECOVERY_ORDER) == set(CANONICAL_FIELD_ORDER)
    assert RECOVERY_ORDER[:2] == ("mister_engage_delay_s", "mister_engage_kpa")
    assert tool.SOURCE_ORDERS == {
        "activation": ACTIVATION_ORDER,
        "recovery": RECOVERY_ORDER,
        "rollback": ROLLBACK_ORDER,
    }


def test_permuted_order_fails_closed_and_emits_no_revision(profiles) -> None:
    edge = treatment_edges(profiles)[0]
    permuted = replace(edge, order=tuple(reversed(ACTIVATION_ORDER)))
    result = tool.replay([permuted], interlock=StubInterlock())
    assert result.fixed_order_ok is False
    assert result.all_pass is False
    assert result.order_revision is None
    assert any("is not the source" in f for f in result.failures)


def test_recovery_order_must_be_the_full_canonical_order(profiles, compiled_defaults) -> None:
    with pytest.raises(ComponentContractError) as excinfo:
        tool.enumerate_prefixes(
            compiled_defaults,
            profiles["baseline"],
            order=TREATMENT_FIELD_ORDER,
            order_name="recovery",
            edge="recovery/bad-order",
            complete_bundle=True,
        )
    assert excinfo.value.code == "recovery_order_not_source_locked"


def test_duplicate_order_entries_are_refused(profiles) -> None:
    with pytest.raises(ComponentContractError) as excinfo:
        tool.enumerate_prefixes(
            profiles["baseline"],
            profiles["moderate"],
            order=ACTIVATION_ORDER + ACTIVATION_ORDER[:1],
            order_name="activation",
            edge="activation/duplicated",
        )
    assert excinfo.value.code == "invalid_component_order"


# ── grid / clamp failure modes ───────────────────────────────────────────────


def test_off_grid_prefix_value_fails_closed(profiles) -> None:
    off_grid = dict(profiles["baseline"])
    # mister_engage_delay_s has a 30 s entity step from a 30 s origin, so 45 is
    # inside every clamp yet not a grid point.
    off_grid["mister_engage_delay_s"] = 45.0
    edge = replace(
        tool.build_edges(profiles, {"drifted": off_grid})[-1],
        edge="recovery/drifted-to-baseline",
    )
    result = tool.replay([edge], interlock=StubInterlock())
    assert result.all_pass is False
    assert result.order_revision is None
    grid_failures = [v for v in result.verdicts if not v.grid_ok]
    assert grid_failures, "an off-grid start must produce grid-invalid prefixes"
    assert all("value_off_entity_grid" in v.detail for v in grid_failures)
    # The prefix that finally repairs the offending component must recover.
    assert result.verdicts[-1].grid_ok is True


def test_off_grid_detail_separates_inherited_from_commanded(profiles) -> None:
    off_grid = dict(profiles["baseline"])
    off_grid["mister_engage_delay_s"] = 45.0
    edge = tool.build_edges(profiles, {"drifted": off_grid})[-1]
    result = tool.replay([edge], interlock=StubInterlock())
    first = next(v for v in result.verdicts if not v.grid_ok)
    assert "inherited from the start state" in first.detail
    assert "COMMANDED BY THE SETTER LIST" not in first.detail


def test_committed_compiled_default_fixture_is_not_entity_grid_clean(compiled_defaults) -> None:
    """Regression pin on the real finding this tool exists to surface.

    The tracked ESPHome constructor fixture boots ``mister_engage_delay_s`` at
    45 s, which is inside every clamp but off the 30 s entity step.  Recovery
    recovery now repairs that field first, so only the inherited prefix-zero
    state is off-grid and the run still must NOT be able to qualify.
    """
    with pytest.raises(ComponentContractError) as excinfo:
        normalize_complete_state(compiled_defaults)
    assert excinfo.value.code == "value_off_entity_grid"
    assert "mister_engage_delay_s" in excinfo.value.detail

    index = RECOVERY_ORDER.index("mister_engage_delay_s")
    profiles_ = tool.load_profiles(tool.DEFAULT_PROFILES)
    edge = tool.build_edges(profiles_, {"compiled-defaults": compiled_defaults})[-1]
    result = tool.replay([edge], interlock=StubInterlock())
    assert result.counts()["grid_fail"] == index + 1
    assert all(v.grid_ok for v in result.verdicts[index + 1 :])
    assert result.order_revision is None


def test_registry_clamp_violation_fails_closed_even_when_grid_valid(profiles) -> None:
    # band_track_fraction is the one component whose registry contract (pinned
    # to 0 by ADR-0004) is tighter than both its firmware clamp and its entity
    # grid, so 0.05 is grid-legal and firmware-legal but registry-illegal.
    definition = REGISTRY["band_track_fraction"]
    assert (definition.min, definition.max) == (0.0, 0.0)
    assert (definition.fw_clamp_lo, definition.fw_clamp_hi) == (0.0, 1.0)
    assert tool.registry_clamp_ok("band_track_fraction", 0.05) is False
    assert tool.firmware_clamp_ok("band_track_fraction", 0.05) is True

    drifted = dict(profiles["baseline"])
    drifted["band_track_fraction"] = 0.05
    normalize_complete_state(drifted)  # grid-valid by construction
    edge = tool.build_edges(profiles, {"pinch-drift": drifted})[-1]
    result = tool.replay([edge], interlock=StubInterlock())
    assert result.all_pass is False
    assert result.order_revision is None
    clamp_failures = [v for v in result.verdicts if not v.registry_clamp_ok]
    assert clamp_failures
    assert all(v.grid_ok for v in clamp_failures), "this failure must be clamp-only, not a grid artefact"
    assert all("registry clamp" in v.detail for v in clamp_failures)


def test_firmware_clamp_violation_is_detected() -> None:
    assert tool.firmware_clamp_ok("temp_hysteresis", 10.0) is False
    assert tool.registry_clamp_ok("temp_hysteresis", 10.0) is False
    assert tool.firmware_clamp_ok("temp_hysteresis", 1.0) is True


def test_clamp_split_is_exactly_the_baseline_bounds_gate() -> None:
    """Drift gate: the two halves must still conjoin to baseline.py::_bounds_ok."""
    for name in CANONICAL_FIELD_ORDER:
        definition = REGISTRY[name]
        if definition.wire_kind == "bool":
            candidates: list[float | bool] = [True, False]
        else:
            lo = definition.fw_clamp_lo if definition.fw_clamp_lo is not None else 0.0
            hi = definition.fw_clamp_hi if definition.fw_clamp_hi is not None else 1.0
            candidates = [lo, hi, lo - 1.0, hi + 1.0, (lo + hi) / 2.0]
        for value in candidates:
            expected = baseline_mod._bounds_ok(definition, value)
            actual = tool.registry_clamp_ok(name, value) and tool.firmware_clamp_ok(name, value)
            assert actual == expected, f"{name}={value!r}"


# ── landing / idempotency ────────────────────────────────────────────────────


def test_every_edge_lands_exactly_on_the_normalized_target(profiles) -> None:
    result = passing_result(profiles)
    assert result.lands_exact_ok is True
    for edge in treatment_edges(profiles):
        cases = tool.enumerate_prefixes(
            edge.start_state, edge.target_state, order=edge.order, order_name=edge.order_name, edge=edge.edge
        )
        assert cases[-1].device_state == normalize_complete_state(edge.target_state)


def test_treatment_edge_whose_start_drifted_on_a_common_field_does_not_land(profiles) -> None:
    drifted = dict(profiles["baseline"])
    # A common (non-treatment) component moved to another legal grid point: the
    # treatment setter list cannot repair it, so the edge cannot land.
    assert "vent_exchange_fraction" in COMMON_FIELDS
    drifted["vent_exchange_fraction"] = 0.35 if profiles["baseline"]["vent_exchange_fraction"] != 0.35 else 0.4
    normalize_complete_state(drifted)
    edge = tool.ReplayEdge(
        edge="activation/drifted-to-moderate",
        kind="activation",
        order_name="activation",
        order=ACTIVATION_ORDER,
        start_label="drifted",
        target_label="moderate",
        start_state=drifted,
        target_state=dict(profiles["moderate"]),
        complete_bundle=False,
    )
    result = tool.replay([edge], interlock=StubInterlock())
    assert result.lands_exact_ok is False
    assert result.all_pass is False
    assert result.order_revision is None
    assert any("not the normalized target" in f for f in result.failures)


def test_idempotency_holds_for_every_permitted_edge(profiles) -> None:
    result = passing_result(profiles)
    assert result.idempotent_ok is True


def test_a_lost_confirmation_breaks_idempotency_and_is_reported(profiles) -> None:
    edge = treatment_edges(profiles)[0]
    cases = tool.enumerate_prefixes(
        edge.start_state, edge.target_state, order=edge.order, order_name=edge.order_name, edge=edge.edge
    )
    # Simulate a case that CLAIMS a component was confirmed while the device
    # state still shows the start value — the exact hazard the check exists for.
    lying = replace(cases[1], device_state=dict(edge.start_state))
    failures: list[str] = []
    assert tool._check_idempotency(edge, [lying], failures) is False
    assert any("re-issued" in f for f in failures)


# ── interlock ────────────────────────────────────────────────────────────────


def test_missing_interlock_probe_is_unproven_not_pass(profiles) -> None:
    result = tool.replay(treatment_edges(profiles), interlock=None)
    assert result.all_pass is False
    assert result.order_revision is None
    assert {v.interlock_safe for v in result.verdicts} == {tool.UNPROVEN}
    assert all(v.ok is False for v in result.verdicts)


def test_unproven_interlock_blocks_even_when_everything_else_passes(profiles) -> None:
    result = tool.replay(treatment_edges(profiles), interlock=StubInterlock(verdict=tool.UNPROVEN))
    assert result.fixed_order_ok and result.idempotent_ok and result.lands_exact_ok
    assert all(v.grid_ok and v.registry_clamp_ok and v.firmware_clamp_ok for v in result.verdicts)
    assert result.all_pass is False
    assert result.order_revision is None


def test_a_single_unsafe_prefix_fails_the_whole_run(profiles) -> None:
    edges = treatment_edges(profiles)
    result = tool.replay(edges, interlock=StubInterlock(unsafe_cases=frozenset({f"{edges[0].edge}/prefix-02"})))
    assert result.all_pass is False
    assert result.order_revision is None
    assert result.counts()["interlock_unsafe"] == 1


def test_default_compiled_probe_cannot_certify_the_full_grid() -> None:
    """The shipped harness is corpus-fed: it can falsify, it cannot certify."""
    source = tool.INVARIANTS_SOURCE.read_text(encoding="utf-8")
    # Coverage is derived from the harness source ∩ the corpus header, so use
    # the union of every sp_* column the harness could possibly read.
    columns = [f"sp_{name}" for name in CANONICAL_FIELD_ORDER] + [
        "sp_temp_low",
        "sp_temp_high",
        "sp_vpd_low",
        "sp_vpd_high",
        "sp_watch_dwell_s",
    ]
    coverage = tool.harness_injection_coverage(source, columns)
    assert set(coverage) < set(CANONICAL_FIELD_ORDER), "coverage must be a strict subset today"
    assert "fog_escalation_kpa" in coverage
    assert set(TREATMENT_FIELD_ORDER) - set(coverage), "most treatment components are not injectable"


def test_compiled_policy_template_declares_an_exact_27_of_48_ceiling() -> None:
    injectable = set(CANONICAL_FIELD_ORDER[:27])
    template = "\n".join(
        f"{name} 1 # wire_id={index + 1}" + ("" if name in injectable else " NOT-IMPOSABLE")
        for index, name in enumerate(CANONICAL_FIELD_ORDER)
    )
    coverage = tool.policy_template_injection_coverage(template)
    assert coverage == frozenset(injectable)
    assert len(set(CANONICAL_FIELD_ORDER) - coverage) == 21


def test_coverage_record_parser_requires_one_machine_record() -> None:
    marker = tool._COVERAGE_MARKER
    payload = {"schema": "verdify-replay-invariants-coverage-v1", "imposed_count": 27}
    assert tool.parse_coverage_payload(f"noise\n{marker} {json.dumps(payload)}\n") == payload
    with pytest.raises(tool.PrefixReplayError):
        tool.parse_coverage_payload("no declaration")
    with pytest.raises(tool.PrefixReplayError):
        tool.parse_coverage_payload(f"{marker} {{}}\n{marker} {{}}\n")


def test_invariant_failures_cannot_collide_with_the_ci_legacy_corpus_exit() -> None:
    source = tool.INVARIANTS_SOURCE.read_text(encoding="utf-8")
    assert "static constexpr int kExitCorpus = 2;" in source
    assert "static constexpr int kExitInvariant = 1;" in source
    assert "return g_stats.counts_by_id.empty() ? 0 : kExitInvariant;" in source


def test_harness_coverage_ignores_columns_the_corpus_does_not_carry() -> None:
    source = 'assign_positive_float("sp_fog_escalation_kpa", sp.fog_escalation_kpa);'
    assert tool.harness_injection_coverage(source, []) == {}
    assert tool.harness_injection_coverage(source, ["sp_fog_escalation_kpa"]) == {
        "fog_escalation_kpa": "sp_fog_escalation_kpa"
    }


def test_unproven_probe_never_reports_safe() -> None:
    probe = tool.UnprovenInterlock("no toolchain")
    case = tool.PrefixCase(
        edge="e", order_name="activation", index=0, device_state={}, applied_fields=(), pending_fields=()
    )
    assert probe.verdict(case).verdict == tool.UNPROVEN
    assert probe.describe()["full_coverage"] is False


def test_external_harness_safe_claim_without_full_coverage_is_downgraded(tmp_path) -> None:
    script = tmp_path / "harness.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "req = json.load(sys.stdin)\n"
        "print(json.dumps({'schema': 'verdify-component-prefix-interlock-verdict-v1',\n"
        "                  'case_id': req['case_id'], 'verdict': 'safe',\n"
        "                  'covered_fields': ['fog_escalation_kpa'], 'detail': 'partial'}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    probe = tool.ExternalHarnessInterlock(script)
    case = tool.PrefixCase(
        edge="e", order_name="activation", index=0, device_state={}, applied_fields=(), pending_fields=()
    )
    outcome = probe.verdict(case)
    assert outcome.verdict == tool.UNPROVEN
    assert "without declaring all 48" in outcome.detail


def test_external_harness_answering_the_wrong_case_is_downgraded(tmp_path) -> None:
    script = tmp_path / "harness.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "json.load(sys.stdin)\n"
        "print(json.dumps({'schema': 'verdify-component-prefix-interlock-verdict-v1',\n"
        "                  'case_id': 'someone-elses-case', 'verdict': 'safe',\n"
        f"                  'covered_fields': {json.dumps(list(CANONICAL_FIELD_ORDER))}}}))\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    probe = tool.ExternalHarnessInterlock(script)
    case = tool.PrefixCase(
        edge="e", order_name="activation", index=0, device_state={}, applied_fields=(), pending_fields=()
    )
    assert probe.verdict(case).verdict == tool.UNPROVEN


# ── ORDER_REVISION derivation ────────────────────────────────────────────────


def test_all_pass_run_emits_a_revision_the_executor_regex_accepts(profiles) -> None:
    result = passing_result(profiles)
    assert result.all_pass is True
    assert result.failures == []
    assert result.order_revision is not None
    assert tool._QUALIFIED_ORDER_REVISION.fullmatch(result.order_revision)
    assert result.order_revision.startswith("prefix-replay-v1:sha256:")


def test_revision_hash_is_deterministic_across_independent_runs(profiles) -> None:
    first = passing_result(profiles)
    second = passing_result(profiles)
    assert first.order_revision == second.order_revision


def test_revision_digest_excludes_wall_clock_and_free_text(profiles) -> None:
    result = passing_result(profiles)
    payload = tool.revision_digest_payload(result)
    assert set(payload) == {"edges", "interlock", "orders", "verdicts"}
    assert payload["orders"] == {
        "activation": list(ACTIVATION_ORDER),
        "recovery": list(RECOVERY_ORDER),
        "rollback": list(ROLLBACK_ORDER),
    }
    for row in payload["verdicts"]:
        assert set(row) == {
            "applied_fields",
            "edge",
            "firmware_clamp_ok",
            "grid_ok",
            "index",
            "interlock_evidence_sha256",
            "interlock_safe",
            "ok",
            "order_name",
            "pending_fields",
            "registry_clamp_ok",
            "state_sha256",
        }
    serialized = tool.canonical_json(payload)
    assert "detail" not in serialized
    assert "computed_at" not in serialized
    assert "result_sha256" not in serialized
    # No ISO-8601 instant may leak into evidence that must re-derive identically.
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", serialized)


def test_any_verdict_change_changes_the_revision(profiles) -> None:
    result = passing_result(profiles)
    mutated = tool.ReplayResult(
        all_pass=True,
        fixed_order_ok=True,
        idempotent_ok=True,
        lands_exact_ok=True,
        verdicts=[replace(result.verdicts[0], grid_ok=False), *result.verdicts[1:]],
        order_revision=None,
        failures=[],
        edges=result.edges,
        interlock=result.interlock,
    )
    assert tool.derive_order_revision(mutated) != result.order_revision


def test_profile_value_change_with_the_same_changed_fields_changes_revision(profiles) -> None:
    changed = {name: dict(values) for name, values in profiles.items()}
    before_fields = [
        change.field_name
        for change in tool.fixed_order_differences(changed["baseline"], changed["moderate"], order=ACTIVATION_ORDER)
    ]
    changed["moderate"]["fog_escalation_kpa"] = 0.4
    after_fields = [
        change.field_name
        for change in tool.fixed_order_differences(changed["baseline"], changed["moderate"], order=ACTIVATION_ORDER)
    ]
    assert after_fields == before_fields
    assert passing_result(changed).order_revision != passing_result(profiles).order_revision


def test_firmware_or_interlock_identity_change_changes_revision(profiles) -> None:
    result = passing_result(profiles)
    changed = replace(
        result,
        interlock={**result.interlock, "binary_sha256": "a" * 64, "harness_source_sha256": "b" * 64},
    )
    assert tool.derive_order_revision(changed) != result.order_revision


def test_per_prefix_interlock_evidence_change_changes_revision(profiles) -> None:
    result = passing_result(profiles)
    verdicts = [replace(result.verdicts[0], interlock_evidence_sha256="c" * 64), *result.verdicts[1:]]
    changed = replace(result, verdicts=verdicts)
    assert tool.derive_order_revision(changed) != result.order_revision


def test_a_different_edge_set_changes_the_revision(profiles) -> None:
    treatment_only = passing_result(profiles)
    with_recovery = tool.replay(
        tool.build_edges(profiles, {"observed": dict(profiles["baseline"])}), interlock=StubInterlock()
    )
    assert with_recovery.all_pass is True
    assert with_recovery.order_revision != treatment_only.order_revision


def test_revision_is_never_derived_from_a_failing_result(profiles) -> None:
    failing = tool.replay(treatment_edges(profiles), interlock=StubInterlock(verdict=tool.UNPROVEN))
    assert failing.order_revision is None
    with pytest.raises(tool.PrefixReplayError):
        tool.derive_order_revision(failing)


def test_empty_edge_set_cannot_qualify_anything() -> None:
    result = tool.replay([], interlock=StubInterlock())
    assert result.all_pass is False
    assert result.order_revision is None
    assert any("empty qualification" in f for f in result.failures)


# ── reporting ────────────────────────────────────────────────────────────────


def test_distinct_failures_collapses_per_case_repetition() -> None:
    grouped = tool.distinct_failures(
        [
            "recovery/x-to-baseline/prefix-00: grid value_off_entity_grid: mister_engage_delay_s=45.0",
            "recovery/x-to-baseline/prefix-01: grid value_off_entity_grid: mister_engage_delay_s=45.0",
            "activation/a-to-b/prefix-00: interlock unproven: no harness",
        ]
    )
    assert len(grouped) == 2
    counts = sorted(count for count, _ in grouped.values())
    assert counts == [1, 2]
    assert all(exemplar.startswith(("recovery/", "activation/")) for _, exemplar in grouped.values())


# ── loading / CLI plumbing ───────────────────────────────────────────────────


def test_profiles_decode_from_the_committed_wire_bytes(profiles) -> None:
    assert set(profiles) == {"baseline", "moderate", "aggressive"}
    for values in profiles.values():
        assert set(values) == set(CANONICAL_FIELD_ORDER)
        assert values == normalize_complete_state(values)


def test_start_state_accepts_both_the_bare_and_wrapped_artifact(tmp_path, profiles) -> None:
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(profiles["baseline"]), encoding="utf-8")
    assert tool.load_start_state(bare, "bare") == profiles["baseline"]

    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(
        json.dumps(
            {
                "device_id": "esp32-vallery",
                "firmware_revision": "2026.8.26.0000.abcdef0",
                "grid_revision": "live-entity-grid-v1:sha256:" + "a" * 64,
                "observed_at": "2026-08-26T01:00:00.000000Z",
                "schema": "verdify-component-current-state-v1",
                "values": profiles["baseline"],
            }
        ),
        encoding="utf-8",
    )
    assert tool.load_start_state(wrapped, "wrapped") == profiles["baseline"]


def test_start_state_refuses_an_incomplete_snapshot(tmp_path, profiles) -> None:
    partial = dict(profiles["baseline"])
    partial.pop("fog_escalation_kpa")
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(partial), encoding="utf-8")
    with pytest.raises(tool.PrefixReplayError):
        tool.load_start_state(path, "partial")


def test_cli_replay_without_a_toolchain_exits_nonzero_and_prints_no_revision(capsys, profiles) -> None:
    exit_code = tool.main(["replay", "--interlock", "none"])
    captured = capsys.readouterr().out
    assert exit_code == 1
    assert "order_revision=NOT EMITTED" in captured
    assert "prefix-replay-v1:sha256:" not in captured
    assert "OVERALL FAIL" in captured
    assert "SUMMARY:" in captured


def test_cli_json_payload_carries_a_reproducible_result_hash(tmp_path, capsys) -> None:
    out = tmp_path / "verdict.json"
    tool.main(["replay", "--interlock", "none", "--json", str(out)])
    capsys.readouterr()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == tool.SCHEMA_VERSION
    assert payload["order_revision"] is None
    assert payload["all_pass"] is False
    assert payload["result_sha256"] == tool.result_sha256(payload)
    # The hash must ignore the wall clock.
    shifted = {**payload, "computed_at": "1999-01-01T00:00:00+00:00"}
    assert tool.result_sha256(shifted) == payload["result_sha256"]
