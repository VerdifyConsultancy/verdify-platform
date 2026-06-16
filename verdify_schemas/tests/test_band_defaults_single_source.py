"""Band-default single-source guard (data-path review issue 1).

verdify_schemas/band_defaults.yaml is THE canonical declaration of the band
defaults. This pins every DERIVED store to it so the three-way divergence that
caused the wet-night orchid regression (registry fallback 0.50 vs live 0.70)
can never silently reappear:

  Guard 1  registry _FW2_* defaults            == YAML            (EXACT)
  Guard 2  db/migrations/177 committed SQL     == generator(YAML) (freshness)
  Guard 3  firmware globals.yaml cold-start band is a DRIER-or-equal fail-safe
           view of the canonical vpd_target, and every band anchor is within the
           registry clamp envelope.

(The CI DB-backed job additionally asserts the seeded crop_band_anchors rows ==
YAML — that needs Postgres and lives with the other DB drift guards.)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from verdify_schemas import tunable_registry as reg
from verdify_schemas.tunable_registry import BAND_DEFAULTS

_REPO = Path(__file__).resolve().parent.parent.parent
_ANCHORS = ("sr", "sm", "ss", "mid")


# ── Guard 1: registry defaults == canonical YAML ────────────────────────────
def test_registry_house_defaults_match_yaml():
    for series, vec in BAND_DEFAULTS["house"].items():
        for anchor, want in zip(_ANCHORS, vec, strict=True):
            got = reg.REGISTRY[f"band_{series}_{anchor}"].default
            assert got == want, f"registry band_{series}_{anchor}={got} != YAML {want}"


def test_registry_zone_defaults_match_yaml():
    for zone, z in BAND_DEFAULTS["zones"].items():
        for anchor, want in zip(_ANCHORS, z["vpd_target"], strict=True):
            got = reg.REGISTRY[f"zone_vpd_target_{zone}_{anchor}"].default
            assert got == want, f"registry zone_vpd_target_{zone}_{anchor}={got} != YAML {want}"
        assert reg.REGISTRY[f"zone_vpd_width_below_{zone}"].default == z["width_below"]
        assert reg.REGISTRY[f"zone_vpd_width_above_{zone}"].default == z["width_above"]
        assert reg.REGISTRY[f"zone_priority_{zone}"].default == float(z["priority"])


# ── Guard 2: committed migration 177 == generator output ────────────────────
def test_migration_177_is_freshly_generated():
    spec = importlib.util.spec_from_file_location("gen_band_seed_sql", _REPO / "scripts" / "gen_band_seed_sql.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    committed = (_REPO / "db" / "migrations" / "177-band-defaults-from-canonical-seed.sql").read_text(encoding="utf-8")
    assert committed == gen.generate(), (
        "db/migrations/177 is stale — re-run `python scripts/gen_band_seed_sql.py --write` "
        "after editing verdify_schemas/band_defaults.yaml"
    )


# ── Guard 3: firmware cold-start is a drier-or-equal, in-clamp fail-safe ─────
def _firmware_globals() -> dict[str, float]:
    raw = yaml.safe_load((_REPO / "firmware" / "greenhouse" / "globals.yaml").read_text(encoding="utf-8"))
    out: dict[str, float] = {}
    for g in raw.get("globals", []):
        if "id" in g and "initial_value" in g and g.get("type") == "float":
            try:
                out[g["id"]] = float(str(g["initial_value"]).strip().strip("'\""))
            except ValueError:
                pass
    return out


def test_firmware_coldstart_vpd_target_is_drier_or_equal():
    # The factory-fresh NVS seed must fail to the SAFE (drier) side for the
    # bare-root Vandas — never wetter than the live band — then the dispatcher
    # reconciles to the DB within one cycle. So firmware vpd_target >= canonical.
    fw = _firmware_globals()
    for anchor, want in zip(_ANCHORS, BAND_DEFAULTS["house"]["vpd_target"], strict=True):
        got = fw[f"band_vpd_target_{anchor}"]
        assert got >= want - 1e-9, f"firmware band_vpd_target_{anchor}={got} is WETTER than canonical {want}"
    for anchor, want in zip(_ANCHORS, BAND_DEFAULTS["zones"]["center"]["vpd_target"], strict=True):
        got = fw[f"zone_vpd_target_center_{anchor}"]
        assert got >= want - 1e-9, f"firmware zone_vpd_target_center_{anchor}={got} is WETTER than canonical {want}"


def test_firmware_band_anchors_within_registry_clamps():
    fw = _firmware_globals()
    for series, (_vec, lo, hi) in reg._FW2_HOUSE_SERIES.items():
        for anchor in _ANCHORS:
            got = fw[f"{series}_{anchor}"]
            assert lo <= got <= hi, f"firmware {series}_{anchor}={got} outside clamp [{lo},{hi}]"
