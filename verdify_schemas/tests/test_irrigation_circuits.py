"""#1336: the physical lines above the relays, and the master-valve rule shared with the firmware."""

from __future__ import annotations

import re
from pathlib import Path

import verdify_schemas.irrigation as irr

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_five_physical_lines_cover_every_schedulable_relay_once() -> None:
    assert [c.circuit_id for c in irr.CIRCUITS] == [
        "wall_drip",
        "center_drip",
        "center_mister",
        "south_mister",
        "west_mister",
    ]
    wired = [line for c in irr.CIRCUITS for line in (c.clean_line, c.fert_line) if line is not None]
    assert sorted(wired) == sorted(irr.LINE_IDS[idx] for idx in irr.SCHEDULABLE_IDX)
    assert len(set(wired)) == len(wired)
    assert irr.MASTER_LINE not in irr.CIRCUIT_OF_LINE and irr.FOG_LINE not in irr.CIRCUIT_OF_LINE


def test_variants_follow_the_relay_kinds_and_partners() -> None:
    for c in irr.CIRCUITS:
        assert irr.LINE_KIND[c.clean_line] == c.kind
        if c.fert_line is not None:
            assert irr.LINE_KIND[c.fert_line] == f"fert_{c.kind}"
            assert irr.CLEAN_PARTNER[c.fert_line] == c.clean_line
    assert irr.CIRCUIT_BY_ID["center_mister"].fert_line is None  # not wired


def test_the_master_valve_opens_only_with_a_fertilized_variant() -> None:
    fertilized = {line for line in irr.LINE_IDS if irr.master_valve_opens(line)}
    assert fertilized == set(irr.CLEAN_PARTNER) == {"L02", "L06", "L08", "L10"}
    assert not any(irr.master_valve_opens(line) for line in ("L01", "L03", "L04", "L05", "L07", "L09", "L11"))


def test_the_firmware_master_rule_names_the_same_relays() -> None:
    header = (REPO_ROOT / "firmware" / "lib" / "irrigation_program.h").read_text()
    match = re.search(r"constexpr uint16_t kFertRelayMask = ([^;]+);", header)
    assert match is not None
    firmware = {f"L{int(n):02d}" for n in re.findall(r"kL(\d\d)", match[1])}
    assert firmware == {line for line in irr.LINE_IDS if irr.master_valve_opens(line)}


def test_circuit_line_maps_a_variant_or_refuses() -> None:
    assert irr.circuit_line("south_mister", "fertilized") == "L08"
    assert irr.circuit_line("wall_drip", "clean") == "L01"
    unwired = irr.circuit_line("center_mister", "fertilized")
    assert isinstance(unwired, irr.Refusal)
    assert (unwired.code, unwired.field) == ("unknown_target", "variant")
    assert "not wired" in unwired.detail
    assert irr.circuit_line("fog", "clean").field == "circuit_id"
    assert irr.circuit_line("wall_drip", "feed").field == "variant"
    assert irr.variant_of("L10") == "fertilized" and irr.variant_of("L09") == "clean" and irr.variant_of("L03") is None


def test_irr_caps_names_what_the_firmware_build_publishes() -> None:
    """#1343 follow-up: kIrrCaps in the engine header is what the API looks for, and the engine yaml publishes it
    beside ${fw_version} as irr_caps."""
    import re
    from pathlib import Path

    from verdify_schemas.irrigation import FOG_RUN_CAP, RUN_ANY_MISTER_CAP, firmware_takes, parse_irr_caps

    root = Path(__file__).resolve().parents[2]
    header = (root / "firmware/lib/irrigation_program.h").read_text()
    caps = re.search(r'constexpr const char\* kIrrCaps = "([^"]+)";', header)
    assert caps is not None
    text = f"fw=2026.11.5.2200.abcdef1 caps={caps[1]}"
    assert {FOG_RUN_CAP, RUN_ANY_MISTER_CAP} <= parse_irr_caps(text).caps
    assert firmware_takes(text, "2026.11.5.2200.abcdef1", FOG_RUN_CAP)
    engine = (root / "firmware/greenhouse/irrigation_engine.yaml").read_text()
    assert "id: irr_caps" in engine and 'std::string("fw=${fw_version} caps=") + verdify_irr::kIrrCaps' in engine
