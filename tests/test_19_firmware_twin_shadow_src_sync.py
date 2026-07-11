"""FW-OPT-6 (#255): the firmware-twin shadow component ships COPIES of the
canonical firmware sources (kustomize's RootOnly load restrictor + ArgoCD forbid
referencing files outside the kustomization dir). This test enforces byte-
identity so a drift between the twin shadow and what the rule-8 replay gate
compiles fails CI — the twin must run the EXACT control code, not a stale copy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPONENT = REPO / "deploy/k8s/components/firmware-twin/src"

# component copy  ->  canonical source
PAIRS = {
    "greenhouse_logic.h": "firmware/lib/greenhouse_logic.h",
    "greenhouse_types.h": "firmware/lib/greenhouse_types.h",
    "greenhouse_solar.h": "firmware/lib/greenhouse_solar.h",
    "replay_emit.cpp": "firmware/test/replay_emit.cpp",
    "offline_driver.py": "twin/offline_driver.py",
}


@pytest.mark.parametrize("copy_name,canonical_rel", PAIRS.items())
def test_twin_shadow_src_is_byte_identical_to_canonical(copy_name: str, canonical_rel: str) -> None:
    copy = COMPONENT / copy_name
    canonical = REPO / canonical_rel
    assert copy.exists(), f"twin shadow source copy missing: {copy}"
    assert canonical.exists(), f"canonical source missing: {canonical}"
    assert copy.read_bytes() == canonical.read_bytes(), (
        f"{copy} has DRIFTED from {canonical}. The firmware-twin shadow must run "
        f"the exact rule-8 control code. Re-copy: cp {canonical_rel} "
        f"deploy/k8s/components/firmware-twin/src/{copy_name}"
    )


def _local_includes(path: Path) -> set[str]:
    return {
        line.split('"')[1]
        for line in path.read_text().splitlines()
        if line.strip().startswith("#include ") and '"' in line
    }


def test_twin_shadow_src_include_closure_is_complete() -> None:
    """2026-07-11 audit: the vendored src was missing greenhouse_solar.h — the
    twin initContainer could never have compiled, and this test passed anyway
    because PAIRS was a hardcoded 4-file list. Walk the local #include closure
    of every vendored C++ file and require each header to be vendored (and
    therefore byte-checked by the test above)."""
    vendored = {p.name for p in COMPONENT.iterdir() if p.suffix in {".h", ".cpp"}}
    missing: dict[str, set[str]] = {}
    for name in sorted(vendored):
        needed = _local_includes(COMPONENT / name)
        absent = needed - vendored
        if absent:
            missing[name] = absent
    assert not missing, (
        f"twin shadow vendored source has unvendored local includes {missing}: "
        "the initContainer g++ compile would fail. Vendor the missing header(s) "
        "from firmware/lib/ and add them to PAIRS."
    )
    # Every vendored C++ file must also be tracked in PAIRS so drift is checked.
    untracked = vendored - set(PAIRS)
    assert not untracked, f"vendored twin sources not drift-tracked in PAIRS: {untracked}"
