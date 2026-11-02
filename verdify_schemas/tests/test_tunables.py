"""Tunable-parameter schema tests.

Every param the dispatcher can emit must appear in ALL_TUNABLES; every
ALL_TUNABLES entry must have a route in entity_map.SETPOINT_MAP. CI drift
is caught here, not post-hoc in production.
"""

from __future__ import annotations

import sys

import pytest
from pydantic import TypeAdapter, ValidationError

from verdify_schemas.tunable_registry import IRRIGATION_SITE_PARAMS, REGISTRY, RETIRED_TUNABLES_REG
from verdify_schemas.tunables import (
    ALL_TUNABLES,
    NUMERIC_TUNABLES,
    SWITCH_TUNABLES,
    TunableParameter,
)


class TestTunableEnumShape:
    def test_numeric_and_switch_are_disjoint(self):
        assert NUMERIC_TUNABLES.isdisjoint(SWITCH_TUNABLES)

    def test_all_tunables_is_union(self):
        assert ALL_TUNABLES == NUMERIC_TUNABLES | SWITCH_TUNABLES

    def test_every_switch_starts_with_sw(self):
        bad = [t for t in SWITCH_TUNABLES if not t.startswith("sw_")]
        assert not bad, f"Switch tunables must start with 'sw_': {bad}"

    def test_no_numeric_starts_with_sw(self):
        bad = [t for t in NUMERIC_TUNABLES if t.startswith("sw_")]
        assert not bad, f"Numeric tunables must NOT start with 'sw_': {bad}"

    def test_tunable_set_matches_entity_map(self):
        """Drift guard: adding a tunable to entity_map but not here = silent drop.

        Resolves entity_map from multiple likely locations:
        - /srv/verdify/ingestor (Iris VM; compat symlink to /mnt/iris/verdify)
        - <repo-root>/ingestor (CI checkout path)
        - /mnt/iris/verdify/ingestor (NFS path)

        Bidirectional assertions:
        - Every SETPOINT_MAP value must be in ALL_TUNABLES (dispatcher can't
          route a param the schema doesn't know).
        - Every ALL_TUNABLES entry must be either dispatched (SETPOINT_MAP) or
          readback-only (CFG_READBACK_MAP). Firmware-internal readback-only
          tunables (e.g. fallback_window_s) need a home in ALL_TUNABLES so
          SetpointSnapshot validates; they have no SETPOINT_MAP route by
          design and are allowed here.
        - A retired row with no firmware presence (no esp_object_id and no
          cfg_readback_object_id, e.g. fog_time_window_start/end, VD-63) has
          no route by design: it stays only so setpoint_changes history
          validates.
        - The irrigation site values (IRRIGATION_SITE_PARAMS, VD-37) have
          neither route either: they travel only inside the irrigation
          program record and are confirmed by its hash.
        """
        import pathlib

        here = pathlib.Path(__file__).resolve()
        repo_root = here.parent.parent.parent  # verdify_schemas/tests/file → repo root
        for p in reversed((str(repo_root / "ingestor"), "/srv/verdify/ingestor", "/mnt/iris/verdify/ingestor")):
            if p not in sys.path:
                sys.path.insert(0, p)
        from entity_map import CFG_READBACK_MAP, SETPOINT_MAP

        em_tunables = set(SETPOINT_MAP.values())
        readback_tunables = set(CFG_READBACK_MAP.values())
        routed_tunables = em_tunables | readback_tunables
        schema_tunables = set(ALL_TUNABLES)
        routeless_retired = {
            name
            for name in RETIRED_TUNABLES_REG
            if REGISTRY[name].esp_object_id is None and REGISTRY[name].cfg_readback_object_id is None
        }

        missing_in_schema = sorted(em_tunables - schema_tunables)
        unrouted_in_schema = sorted(schema_tunables - routed_tunables - routeless_retired - IRRIGATION_SITE_PARAMS)

        assert not missing_in_schema, (
            f"entity_map has tunables the schema doesn't know: {missing_in_schema}. Add to verdify_schemas/tunables.py."
        )
        assert not unrouted_in_schema, (
            f"schema has tunables neither SETPOINT_MAP nor CFG_READBACK_MAP routes: {unrouted_in_schema}. "
            "Remove from schema or add to one of the entity_map maps."
        )
        # The program-bound site values must stay unrouted: the dispatcher never pushes them.
        assert IRRIGATION_SITE_PARAMS <= schema_tunables
        assert not IRRIGATION_SITE_PARAMS & routed_tunables


class TestPhysicsInvariants:
    """Sprint 24: `_PHYSICS_INVARIANTS` drift guard. Every key the dispatcher
    clamps must be a canonical ALL_TUNABLES entry; non-canonical names would
    fail SetpointChange validation upstream and never reach the invariant
    check, making those entries dead defense. Catches the Sprint 18→23 drift
    (fog_window_start vs fog_time_window_start, vpd_max_safe, etc.) at CI time.
    """

    def test_physics_invariants_are_canonical(self):
        import pathlib

        # ingestor/tasks.py imports asyncpg; the schema-only CI env doesn't
        # ship runtime deps, so skip cleanly instead of ModuleNotFoundError.
        # Local `make test` (which installs ingestor deps) still runs this.
        pytest.importorskip("asyncpg")

        here = pathlib.Path(__file__).resolve()
        repo_root = here.parent.parent.parent
        ingestor_path = str(repo_root / "ingestor")

        # Worktree-safe: prefer THIS repo's ingestor module. Unlike entity_map
        # (which stays in lockstep across worktrees), _PHYSICS_INVARIANTS can
        # differ per branch during a sprint, so we must import from the same
        # commit as this test file. Force-reorder sys.path + clear cached import.
        if ingestor_path in sys.path:
            sys.path.remove(ingestor_path)
        sys.path.insert(0, ingestor_path)
        sys.modules.pop("tasks", None)

        from tasks import _PHYSICS_INVARIANTS

        invariant_keys = set(_PHYSICS_INVARIANTS.keys())
        non_canonical = sorted(invariant_keys - set(ALL_TUNABLES))
        assert not non_canonical, (
            f"_PHYSICS_INVARIANTS has non-canonical keys: {non_canonical}. "
            "Rename to match ALL_TUNABLES or remove. Dead keys are silently dead defense."
        )


class TestTunableParameterValidator:
    def test_accepts_known_numeric(self):
        adapter = TypeAdapter(TunableParameter)
        assert adapter.validate_python("temp_low") == "temp_low"

    def test_accepts_known_switch(self):
        adapter = TypeAdapter(TunableParameter)
        assert adapter.validate_python("sw_economiser_enabled") == "sw_economiser_enabled"

    def test_rejects_unknown(self):
        adapter = TypeAdapter(TunableParameter)
        with pytest.raises(ValidationError, match="Unknown tunable parameter"):
            adapter.validate_python("something_fake")

    def test_rejects_case_mismatch(self):
        adapter = TypeAdapter(TunableParameter)
        with pytest.raises(ValidationError):
            adapter.validate_python("TEMP_LOW")
