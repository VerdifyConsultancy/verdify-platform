"""Irrigation program record VIP1 and text-sensor grammar tests (VD-36, #1151).

The layout, the fixed codes, the seed program and its header are asserted by hand here, never
read back from the golden vectors; the vectors are then checked byte for byte against the packer,
and the committed fixture against its generator (``scripts/gen-irrigation-program-vectors.py``).
The firmware decoder (#1159) runs its native tests on the same fixture, so a change here is a
change to the controller contract.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import random
import struct
import subprocess
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import get_args

import pytest
import yaml
from pydantic import ValidationError

import verdify_schemas
from verdify_schemas import irrigation as irr
from verdify_schemas import operations
from verdify_schemas.irrigation import (
    DEGRADED_ONLY_IDX,
    DEVICE_IDX,
    IrrEvent,
    IrrFault,
    IrrigationCause,
    IrrRun,
    IrrState,
    IrrWaiting,
    ProgramId,
    ProgramRecord,
    ProgramRecordError,
    RecordLine,
    RecordSlot,
    RunOutcome,
    VpdSources,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "irrigation_program_vectors.json"
MODULE_PATH = REPO_ROOT / "verdify_schemas" / "irrigation.py"
GENERATOR = REPO_ROOT / "scripts" / "gen-irrigation-program-vectors.py"
OPENAPI = REPO_ROOT / "docs" / "design" / "dashboard" / "openapi" / "dashboard-api.v1.yaml"

# irrigation §3: each line's kind. §4.3: each kind's schedule bounds (inclusive), copied by hand so
# the vectors are checked against the design rather than against #1152's BOUNDS_BY_KIND.
LINE_KIND = {
    "L01": "drip",
    "L02": "fert_drip",
    "L03": "master",
    "L04": "mister",
    "L05": "drip",
    "L06": "fert_drip",
    "L07": "mister",
    "L08": "fert_mister",
    "L09": "mister",
    "L10": "fert_mister",
    "L11": "fog",
}
KIND_BOUNDS = {
    "drip": {"on_s": (10, 7200), "count": (1, 1), "gap_s": (0, 0), "prewet_s": (0, 0), "flush_s": (0, 0)},
    "fert_drip": {"on_s": (10, 7200), "count": (1, 1), "gap_s": (0, 0), "prewet_s": (0, 1800), "flush_s": (0, 1800)},
    "mister": {"on_s": (10, 90), "count": (1, 60), "gap_s": (30, 600), "prewet_s": (0, 0), "flush_s": (0, 0)},
    "fert_mister": {
        "on_s": (10, 90),
        "count": (1, 60),
        "gap_s": (30, 600),
        "prewet_s": (0, 1800),
        "flush_s": (0, 1800),
    },
}

SEED_HEADER_HEX = "5649503168015a0f0900010003000000"


def _seed_by_hand() -> ProgramRecord:
    """The program M3 (#1153) seeds, written out from irrigation §3 and §4."""
    lines = [RecordLine() for _ in range(11)]
    lines[0] = RecordLine(daily_cap_min=60)  # L01
    lines[3] = RecordLine(daily_cap_min=120, vpd_reserve_min=60)  # L04
    lines[4] = RecordLine(daily_cap_min=30)  # L05
    lines[6] = RecordLine(daily_cap_min=45, vpd_reserve_min=15)  # L07
    lines[8] = RecordLine(daily_cap_min=45, vpd_reserve_min=15)  # L09
    slots = tuple(
        RecordSlot(
            slot_id=slot_id,
            line=3,  # L04
            weekday_mask=0x7F,
            start_kind=2,  # solar_noon
            degraded_only=True,
            start=offset,
            on_s=45,
            gap_s=60,
            count=4,
        )
        for slot_id, offset in ((1, -120), (2, 0), (3, 120))
    )
    return ProgramRecord(
        saf4_gal=360,
        rh_ceiling_pct=90,
        climate_yield_min=15,
        enabled_mask=0b1001,  # L01, L04
        vpd_member_mask=0b001,  # L04
        schedule_member_mask=0,
        lines=tuple(lines),
        slots=slots,
    )


def _zero_lines() -> tuple[RecordLine, ...]:
    return (RecordLine(),) * 11


@pytest.fixture(scope="module")
def vectors() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture(scope="module")
def by_name(vectors: dict) -> dict[str, dict]:
    return {vector["name"]: vector for vector in vectors["vectors"]}


# ── Layout and codes, by hand ────────────────────────────────────────────────


def test_layout_is_584_little_endian_bytes() -> None:
    assert irr.PROGRAM_MAGIC == b"VIP1"
    assert (irr.PROGRAM_HEADER_FORMAT, irr.PROGRAM_LINE_FORMAT, irr.PROGRAM_SLOT_FORMAT) == (
        "<4sHBBHBBB3x",
        "<HHI",
        "<HBBBBhHHHHB3x",
    )
    assert struct.calcsize("<4sHBBHBBB3x") == 16
    assert struct.calcsize("<HHI") == 8
    assert struct.calcsize("<HBBBBhHHHHB3x") == 20
    assert (irr.PROGRAM_LINES_OFFSET, irr.PROGRAM_SLOTS_OFFSET, irr.PROGRAM_RECORD_LEN) == (16, 104, 584)
    assert (irr.PROGRAM_LINE_COUNT, irr.PROGRAM_MAX_SLOTS, irr.PROGRAM_CHUNK_MAX) == (11, 24, 192)
    assert irr.SLOT_FLAG_DEGRADED_ONLY == 0x01


def test_fixed_codes() -> None:
    assert dict(DEVICE_IDX) == {f"L{n:02d}": n - 1 for n in range(1, 12)}
    assert irr.LINE_IDS == tuple(f"L{n:02d}" for n in range(1, 12))
    assert dict(irr.MISTER_MASK_BIT) == {"L04": 0, "L07": 1, "L09": 2}
    assert dict(irr.START_KIND) == {"clock": 0, "sunrise": 1, "solar_noon": 2, "sunset": 3}
    assert dict(irr.START_RANGE) == {0: (0, 1439), 1: (-240, 240), 2: (-240, 240), 3: (-240, 240)}
    assert irr.SCHEDULABLE_IDX == {
        DEVICE_IDX[x] for x in ("L01", "L02", "L04", "L05", "L06", "L07", "L08", "L09", "L10")
    }
    assert irr.DRIP_MANIFOLD_IDX == {DEVICE_IDX[x] for x in ("L01", "L02", "L05", "L06")}
    assert irr.MIST_MANIFOLD_IDX == {DEVICE_IDX[x] for x in ("L04", "L07", "L08", "L09", "L10")}
    assert irr.DRIP_MANIFOLD_IDX | irr.MIST_MANIFOLD_IDX == irr.SCHEDULABLE_IDX
    assert not irr.DRIP_MANIFOLD_IDX & irr.MIST_MANIFOLD_IDX
    assert DEGRADED_ONLY_IDX == {DEVICE_IDX[x] for x in ("L04", "L07", "L09")}
    assert set(LINE_KIND) == set(irr.LINE_IDS)
    assert {DEVICE_IDX[x] for x, kind in LINE_KIND.items() if kind in KIND_BOUNDS} == irr.SCHEDULABLE_IDX
    with pytest.raises(TypeError):
        irr.DEVICE_IDX["L12"] = 11  # type: ignore[index]


def test_seed_header_and_first_slot_by_hand() -> None:
    data = irr.pack_program(_seed_by_hand())
    assert len(data) == 584
    assert data[:16].hex() == SEED_HEADER_HEX
    # L04's line entry: cap 120, reserve 60, not paused.
    assert data[16 + 3 * 8 : 16 + 4 * 8].hex() == "78003c0000000000"
    # Slot 1: id 1, L04, every day, solar noon, degraded_only, -120, on 45, gap 60, no prewet or flush, count 4.
    assert data[104:124].hex() == "0100037f020188ff2d003c000000000004000000"
    assert data[104 + 3 * 20 :] == bytes(21 * 20)  # unused slots are zero


def test_every_record_is_584_bytes_and_chunks_by_hand(by_name: dict[str, dict]) -> None:
    records = [irr.pack_program(_seed_by_hand())] + [bytes.fromhex(v["hex"]) for v in by_name.values()]
    for data in records:
        assert len(data) == 584
        chunks = irr.program_chunks(data)
        assert [offset for offset, _ in chunks] == [0, 192, 384, 576]
        assert [len(bytes.fromhex(chunk)) for _, chunk in chunks] == [192, 192, 192, 8]
        assert all(chunk == chunk.lower() for _, chunk in chunks)
        assert "".join(chunk for _, chunk in chunks) == data.hex()
    small = irr.program_chunks(records[0], max_len=100)
    assert [offset for offset, _ in small] == [0, 100, 200, 300, 400, 500]
    for bad in (0, 193):
        with pytest.raises(ValueError):
            irr.program_chunks(records[0], max_len=bad)


def test_hashes_cover_all_584_bytes() -> None:
    data = irr.pack_program(_seed_by_hand())
    digest = irr.program_sha256(data)
    assert digest == hashlib.sha256(data).hexdigest()
    assert len(digest) == 64 and digest == digest.lower()
    assert irr.program_hash16(data) == digest[:16]
    assert irr.program_sha256(bytearray(data)) == irr.program_sha256(memoryview(data)) == digest
    for wrong in (data[:-1], data + b"\x00", b""):
        with pytest.raises(ProgramRecordError) as raised:
            irr.program_sha256(wrong)
        assert raised.value.code == "bad_length"
    with pytest.raises(TypeError):
        irr.program_sha256(data.hex())  # type: ignore[arg-type]


# ── Golden vectors ───────────────────────────────────────────────────────────


def test_golden_vectors_reproduce_bytes_and_hashes(vectors: dict) -> None:
    assert (vectors["record_len"], vectors["chunk_max"]) == (584, 192)
    assert [vector["name"] for vector in vectors["vectors"]] == ["empty", "seed", "full"]
    for vector in vectors["vectors"]:
        record = ProgramRecord.model_validate(vector["program"])
        data = irr.pack_program(record)
        assert data.hex() == vector["hex"], vector["name"]
        assert irr.program_sha256(data) == vector["sha256"] == hashlib.sha256(data).hexdigest()
        assert [list(chunk) for chunk in irr.program_chunks(data)] == vector["chunks"]
        assert irr.unpack_program(bytes.fromhex(vector["hex"])) == record
        assert record.model_dump(mode="json") == vector["program"]


def test_vectors_fixture_is_current() -> None:
    """The committed JSON is exactly what the generator writes (also run by scripts/ci-local.sh)."""
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"], capture_output=True, text=True, cwd=REPO_ROOT, check=False
    )
    assert result.returncode == 0, f"irrigation vectors drifted:\n{result.stdout}{result.stderr}"


def test_seed_and_empty_vectors_by_hand(by_name: dict[str, dict]) -> None:
    assert ProgramRecord.model_validate(by_name["seed"]["program"]) == _seed_by_hand()
    assert by_name["seed"]["hex"].startswith(SEED_HEADER_HEX)
    empty = ProgramRecord(saf4_gal=360, rh_ceiling_pct=90, climate_yield_min=15, lines=_zero_lines())
    assert ProgramRecord.model_validate(by_name["empty"]["program"]) == empty
    assert bytes.fromhex(by_name["empty"]["hex"]) == bytes.fromhex("5649503168015a0f0000000000000000") + bytes(568)


def test_full_vector_covers_the_record(by_name: dict[str, dict]) -> None:
    full = ProgramRecord.model_validate(by_name["full"]["program"])
    assert full.slot_count == 24
    assert full.slots[-1].slot_id == 65535
    assert {slot.line for slot in full.slots} == irr.SCHEDULABLE_IDX
    assert {slot.start_kind for slot in full.slots} == {0, 1, 2, 3}
    assert any(slot.start_kind != 0 and slot.start < 0 for slot in full.slots)
    degraded = {slot.line for slot in full.slots if slot.degraded_only}
    assert degraded and degraded <= DEGRADED_ONLY_IDX
    assert full.enabled_mask == 0x7FF
    assert full.vpd_member_mask == full.schedule_member_mask == 0x07
    assert all(line.paused_until for line in full.lines)

    def kind_of(slot: RecordSlot) -> str:
        return LINE_KIND[irr.LINE_IDS[slot.line]]

    drips = [s for s in full.slots if kind_of(s) in ("drip", "fert_drip")]
    misters = [s for s in full.slots if kind_of(s) in ("mister", "fert_mister")]
    fert = [s for s in full.slots if kind_of(s).startswith("fert_")]
    assert any(s.on_s == 7200 and s.count == 1 and s.gap_s == 0 for s in drips)
    assert any(s.on_s == 90 and s.count == 60 and s.gap_s == 600 for s in misters)
    assert any(s.prewet_s == 1800 and s.flush_s == 1800 for s in fert)


def test_vector_slots_inside_their_line_kind_bounds(by_name: dict[str, dict]) -> None:
    """irrigation §4.3: the firmware decoder (#1159) checks these too, so every vector stays inside."""
    for name, vector in by_name.items():
        for slot in ProgramRecord.model_validate(vector["program"]).slots:
            line_id = irr.LINE_IDS[slot.line]
            for field, (lo, hi) in KIND_BOUNDS[LINE_KIND[line_id]].items():
                assert lo <= getattr(slot, field) <= hi, (name, slot.slot_id, line_id, field)


def _random_record(rng: random.Random) -> ProgramRecord:
    lines = []
    for _ in range(11):
        cap = rng.choice((0, 480, rng.randint(0, 480)))
        paused_until = rng.choice((0, 0xFFFF_FFFF, rng.randint(1, 0xFFFF_FFFF)))
        lines.append(RecordLine(daily_cap_min=cap, vpd_reserve_min=rng.randint(0, cap), paused_until=paused_until))
    slots = []
    for slot_id in sorted(rng.sample(range(1, 65536), rng.randint(0, 24))):
        line = rng.choice(sorted(irr.SCHEDULABLE_IDX))
        kind = rng.randint(0, 3)
        lo, hi = irr.START_RANGE[kind]
        slots.append(
            RecordSlot(
                slot_id=slot_id,
                line=line,
                weekday_mask=rng.randint(1, 0x7F),
                start_kind=kind,
                degraded_only=line in DEGRADED_ONLY_IDX and rng.random() < 0.5,
                start=rng.choice((lo, hi, rng.randint(lo, hi))),
                on_s=rng.randint(10, 7200),
                gap_s=rng.randint(0, 600),
                prewet_s=rng.randint(0, 1800),
                flush_s=rng.randint(0, 1800),
                count=rng.randint(1, 60),
            )
        )
    return ProgramRecord(
        saf4_gal=rng.randint(100, 600),
        rh_ceiling_pct=rng.randint(75, 98),
        climate_yield_min=rng.randint(5, 30),
        enabled_mask=rng.randint(0, 0x7FF),
        vpd_member_mask=rng.randint(0, 7),
        schedule_member_mask=rng.randint(0, 7),
        lines=tuple(lines),
        slots=tuple(slots),
    )


def test_unpack_inverts_pack_for_valid_records() -> None:
    rng = random.Random(1151)  # noqa: S311 - deterministic test data, not security
    for _ in range(300):
        record = _random_record(rng)
        data = irr.pack_program(record)
        assert len(data) == 584
        assert irr.unpack_program(data) == record
        assert irr.pack_program(irr.unpack_program(data)) == data


# ── unpack_program rejections ────────────────────────────────────────────────


def _seed_with(*patches: tuple[int, str, int]) -> bytes:
    data = bytearray(irr.pack_program(_seed_by_hand()))
    for offset, fmt, value in patches:
        struct.pack_into(fmt, data, offset, value)
    return bytes(data)


SLOT0 = 104


def _slot0(field_offset: int, fmt: str, value: int) -> bytes:
    return _seed_with((SLOT0 + field_offset, fmt, value))


REJECTIONS: list[tuple[str, Callable[[], bytes], str]] = [
    ("short record (583 B)", lambda: irr.pack_program(_seed_by_hand())[:583], "bad_length"),
    ("long record (585 B)", lambda: irr.pack_program(_seed_by_hand()) + b"\x00", "bad_length"),
    ("no bytes", lambda: b"", "bad_length"),
    ("wrong magic", lambda: b"VIP2" + irr.pack_program(_seed_by_hand())[4:], "bad_magic"),
    ("lower-case magic", lambda: b"vip1" + irr.pack_program(_seed_by_hand())[4:], "bad_magic"),
    ("non-zero reserved header byte 13", lambda: _seed_with((13, "B", 1)), "bad_header"),
    ("non-zero reserved header byte 15", lambda: _seed_with((15, "B", 0x80)), "bad_header"),
    ("slot_count over 24", lambda: _seed_with((12, "B", 25)), "bad_header"),
    ("enabled-mask bit 11", lambda: _seed_with((8, "<H", 0x0809)), "bad_header"),
    ("VPD-member-mask bit 3", lambda: _seed_with((10, "B", 0x09)), "bad_header"),
    ("schedule-member-mask bit 7", lambda: _seed_with((11, "B", 0x80)), "bad_header"),
    ("SAF-4 below 100", lambda: _seed_with((4, "<H", 99)), "bad_header"),
    ("SAF-4 above 600", lambda: _seed_with((4, "<H", 601)), "bad_header"),
    ("RH ceiling below 75", lambda: _seed_with((6, "B", 74)), "bad_header"),
    ("RH ceiling above 98", lambda: _seed_with((6, "B", 99)), "bad_header"),
    ("climate yield below 5", lambda: _seed_with((7, "B", 4)), "bad_header"),
    ("climate yield above 30", lambda: _seed_with((7, "B", 31)), "bad_header"),
    ("daily_cap_min above 480", lambda: _seed_with((16, "<H", 481)), "bad_line"),
    ("vpd_reserve_min above the cap", lambda: _seed_with((16 + 3 * 8 + 2, "<H", 121)), "bad_line"),
    ("non-zero unused slot", lambda: _seed_with((12, "B", 2)), "bad_slot"),
    ("non-zero byte at the end of an unused slot", lambda: _seed_with((583, "B", 1)), "bad_slot"),
    ("used slot with slot_id 0", lambda: _seed_with((12, "B", 4)), "bad_slot"),
    ("slot_id 0 in the first slot", lambda: _slot0(0, "<H", 0), "bad_slot"),
    ("repeated slot_id", lambda: _seed_with((SLOT0 + 20, "<H", 1)), "bad_slot"),
    ("slots out of slot_id order", lambda: _slot0(0, "<H", 5), "bad_slot"),
    ("non-zero reserved slot byte", lambda: _slot0(17, "B", 1), "bad_slot"),
    ("slot on L03", lambda: _slot0(2, "B", 2), "bad_slot"),
    ("slot on L11", lambda: _slot0(2, "B", 10), "bad_slot"),
    ("slot on index 11", lambda: _slot0(2, "B", 11), "bad_slot"),
    ("weekday mask 0", lambda: _slot0(3, "B", 0), "bad_slot"),
    ("weekday mask bit 7", lambda: _slot0(3, "B", 0xFF), "bad_slot"),
    ("unknown start kind", lambda: _slot0(4, "B", 4), "bad_slot"),
    ("flag bit 1", lambda: _slot0(5, "B", 0x03), "bad_slot"),
    ("degraded_only slot on L01", lambda: _slot0(2, "B", 0), "bad_slot"),
    ("degraded_only slot on L08", lambda: _slot0(2, "B", 7), "bad_slot"),
    ("solar offset +241", lambda: _slot0(6, "<h", 241), "bad_slot"),
    ("solar offset -241", lambda: _slot0(6, "<h", -241), "bad_slot"),
    ("clock minute 1440", lambda: _seed_with((SLOT0 + 4, "B", 0), (SLOT0 + 6, "<h", 1440)), "bad_slot"),
    ("clock minute -1", lambda: _seed_with((SLOT0 + 4, "B", 0), (SLOT0 + 6, "<h", -1)), "bad_slot"),
    ("on_s 9", lambda: _slot0(8, "<H", 9), "bad_slot"),
    ("on_s 7201", lambda: _slot0(8, "<H", 7201), "bad_slot"),
    ("gap_s 601", lambda: _slot0(10, "<H", 601), "bad_slot"),
    ("prewet_s 1801", lambda: _slot0(12, "<H", 1801), "bad_slot"),
    ("flush_s 1801", lambda: _slot0(14, "<H", 1801), "bad_slot"),
    ("count 0", lambda: _slot0(16, "B", 0), "bad_slot"),
    ("count 61", lambda: _slot0(16, "B", 61), "bad_slot"),
]


@pytest.mark.parametrize(("case", "make", "code"), REJECTIONS, ids=[case for case, _, _ in REJECTIONS])
def test_unpack_rejects_records_outside_the_layout(case: str, make: Callable[[], bytes], code: str) -> None:
    with pytest.raises(ValueError) as raised:
        irr.unpack_program(make())
    assert isinstance(raised.value, ProgramRecordError)
    assert raised.value.code == code, str(raised.value)
    assert str(raised.value).startswith(f"{code}: ")


def test_unpack_reports_rejects_in_decoder_order() -> None:
    """Length, magic, header, lines, then slots: the order the firmware decoder reports them in."""
    bad_everywhere = bytearray(_seed_with((4, "<H", 99), (16, "<H", 481), (SLOT0 + 2, "B", 2)))
    assert irr.REJECT_CODES == ("bad_length", "bad_magic", "bad_hash", "bad_header", "bad_line", "bad_slot")
    with pytest.raises(ProgramRecordError, match="^bad_header"):
        irr.unpack_program(bytes(bad_everywhere))
    struct.pack_into("<H", bad_everywhere, 4, 360)
    with pytest.raises(ProgramRecordError, match="^bad_line"):
        irr.unpack_program(bytes(bad_everywhere))
    struct.pack_into("<H", bad_everywhere, 16, 60)
    with pytest.raises(ProgramRecordError, match="^bad_slot"):
        irr.unpack_program(bytes(bad_everywhere))
    error = ProgramRecordError("bad_slot", "slot 0: example")
    assert copy.deepcopy(error).code == "bad_slot"  # reconstructs through __reduce__, as pickling does


def test_unpack_and_pack_reject_non_bytes() -> None:
    with pytest.raises(TypeError):
        irr.unpack_program(SEED_HEADER_HEX)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        irr.unpack_program(584)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        irr.pack_program(_seed_by_hand().model_dump())  # type: ignore[arg-type]


MODEL_REJECTIONS: list[tuple[str, Callable[[], object]]] = [
    ("reserve above cap", lambda: RecordLine(daily_cap_min=10, vpd_reserve_min=11)),
    ("cap above 480", lambda: RecordLine(daily_cap_min=481)),
    ("paused_until beyond u32", lambda: RecordLine(paused_until=2**32)),
    ("slot on L03", lambda: RecordSlot(slot_id=1, line=2, weekday_mask=1, start_kind=0, start=0, on_s=10)),
    ("degraded_only on L02", lambda: _slot(line=1, degraded_only=True)),
    ("fractional on_s", lambda: _slot(on_s=45.0)),
    ("bool as a number", lambda: _slot(count=True)),
    ("string as a number", lambda: _slot(on_s="45")),
    ("unknown field", lambda: _slot(revision=1)),
    (
        "ten lines",
        lambda: ProgramRecord(saf4_gal=360, rh_ceiling_pct=90, climate_yield_min=15, lines=(RecordLine(),) * 10),
    ),
    ("no lines", lambda: ProgramRecord(saf4_gal=360, rh_ceiling_pct=90, climate_yield_min=15)),
    ("25 slots", lambda: _program(slots=tuple(_slot(slot_id=n) for n in range(1, 26)))),
    ("descending slots", lambda: _program(slots=(_slot(slot_id=2), _slot(slot_id=1)))),
    ("repeated slot", lambda: _program(slots=(_slot(slot_id=2), _slot(slot_id=2)))),
]


def _slot(**overrides: object) -> RecordSlot:
    fields: dict[str, object] = {"slot_id": 1, "line": 0, "weekday_mask": 1, "start_kind": 0, "start": 0, "on_s": 10}
    return RecordSlot(**(fields | overrides))


def _program(**overrides: object) -> ProgramRecord:
    fields: dict[str, object] = {"saf4_gal": 360, "rh_ceiling_pct": 90, "climate_yield_min": 15, "lines": _zero_lines()}
    return ProgramRecord(**(fields | overrides))


@pytest.mark.parametrize(("case", "make"), MODEL_REJECTIONS, ids=[case for case, _ in MODEL_REJECTIONS])
def test_record_models_reject_what_unpack_rejects(case: str, make: Callable[[], object]) -> None:
    with pytest.raises(ValidationError):
        make()


def test_record_models_are_frozen_and_carry_no_revision_skip_or_flow_field() -> None:
    record = _seed_by_hand()
    with pytest.raises(ValidationError):
        record.saf4_gal = 400  # type: ignore[misc]
    assert hash(record) == hash(_seed_by_hand())
    for model in (ProgramRecord, RecordLine, RecordSlot):
        for field in model.model_fields:
            assert not any(word in field for word in ("revision", "skip", "flow")), (model.__name__, field)


# ── Enums and codes ──────────────────────────────────────────────────────────


def test_enums_match_the_openapi_contract() -> None:
    causes = ["vpd", "schedule", "run_now", "feed", "prewet", "flush", "fallback", "unknown"]
    outcomes = ["ok", "short", "stopped", "preempted", "skipped", "missed", "cap", "unknown"]
    assert [cause.value for cause in IrrigationCause] == causes
    assert [outcome.value for outcome in RunOutcome] == outcomes
    schemas = yaml.safe_load(OPENAPI.read_text())["components"]["schemas"]
    assert schemas["IrrigationCause"]["enum"] == causes
    assert schemas["RunOutcome"]["enum"] == outcomes


def test_run_outcome_is_seven_device_outcomes_plus_unknown() -> None:
    assert dict(irr.OUTCOME_CODES) == {
        "ok": RunOutcome.OK,
        "sh": RunOutcome.SHORT,
        "st": RunOutcome.STOPPED,
        "pe": RunOutcome.PREEMPTED,
        "sk": RunOutcome.SKIPPED,
        "mi": RunOutcome.MISSED,
        "ca": RunOutcome.CAP,
    }
    assert set(irr.OUTCOME_CODES.values()) == set(RunOutcome) - {RunOutcome.UNKNOWN}
    for code, outcome in irr.OUTCOME_CODES.items():
        assert irr.parse_irr_events(f"1,0,4,20759,{code}")[0].outcome is outcome
    for code in ("un", "uk", "unknown", "xx", "OK", "Ok", ""):
        with pytest.raises(ValueError):
            irr.parse_irr_events(f"1,0,4,20759,{code}")
    with pytest.raises(ValidationError):
        IrrEvent(seq=1, line_id="L01", slot_id=4, local_day=date(2026, 11, 2), outcome="unknown")


def test_reason_words() -> None:
    assert dict(irr.REASON_WORDS) == {
        "dis": "line disabled",
        "pau": "paused",
        "nsm": "not a schedule member",
        "nvm": "not a VPD member",
        "vok": "VPD sensing is healthy",
        "bof": "backing off",
        "flt": "faulted",
        "occ": "someone inside (Frigate)",
        "cap": "cap reached",
        "res": "VPD reserve",
        "rh": "RH limit",
        "dew": "dew limit",
        "clk": "no clock",
        "man": "waiting for the manifold",
        "sf": "sensor fault",
        "bud": "soft budget",
        "saf": "SAF-4 ceiling",
        "l03": "L03 open",
        "rst": "controller reset",
        "cmd": "stopped by a command",
    }
    assert get_args(irr.ReasonCode) == tuple(irr.REASON_WORDS)


# ── irr_program_id ───────────────────────────────────────────────────────────


def test_program_id_samples_round_trip(vectors: dict, by_name: dict[str, dict]) -> None:
    samples = {sample["name"]: sample for sample in vectors["text"]["irr_program_id"]}
    assert samples["seed_nvs"]["text"] == f"h={by_name['seed']['sha256'][:16]} src=nvs"
    assert samples["rom_default"]["text"] == "h=0000000000000000 src=rom"
    for sample in samples.values():
        parsed = irr.parse_irr_program_id(sample["text"])
        assert parsed.model_dump(mode="json") == sample["parsed"]
        assert irr.format_irr_program_id(parsed) == sample["text"]
    assert irr.parse_irr_program_id("h=7ae03efe564082b7 src=nvs") == ProgramId(hash16="7ae03efe564082b7", src="nvs")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "h=7ae03efe564082b7",
        "h=7ae03efe564082b7 src=flash",
        "h=7AE03EFE564082B7 src=nvs",
        "h=7ae03efe564082b src=nvs",
        "h=7ae03efe564082b7a src=nvs",
        "src=nvs h=7ae03efe564082b7",
        "h=7ae03efe564082b7 src=nvs ",
        "h=7ae03efe564082b7  src=nvs",
        "h=7ae03efe564082b7 src=rom",
        "h=7ae03efe564082b7 src=nvs x=1",
    ],
)
def test_program_id_rejects_text_outside_the_grammar(text: str) -> None:
    with pytest.raises(ValueError):
        irr.parse_irr_program_id(text)


def test_parsers_reject_non_text() -> None:
    for parse in (irr.parse_irr_program_id, irr.parse_irr_state, irr.parse_irr_events):
        with pytest.raises(TypeError):
            parse(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            parse("v=hoo " * 50)  # over 255 characters


# ── irr_state ────────────────────────────────────────────────────────────────


def test_state_samples_round_trip(vectors: dict) -> None:
    samples = vectors["text"]["irr_state"]
    assert {"empty", "fallback_mist", "feed_prewet_waiting", "overflow_drops_waiting"} <= {s["name"] for s in samples}
    for sample in samples:
        parsed = irr.parse_irr_state(sample["text"])
        assert parsed.model_dump(mode="json") == sample["parsed"], sample["name"]
        assert irr.format_irr_state(parsed) == sample["text"], sample["name"]
        assert len(sample["text"]) <= 255


def test_state_parses_each_token_kind() -> None:
    state = irr.parse_irr_state(
        "d=0,prewet,5,1793628000,1793628120 m=3,vpd,0,1793628001,1793628046 w=3,occ w=6,man,1793628120 "
        "f=8,b,1793628600 f=9,l,1793689200 v=hpo j=bad_hash"
    )
    drip, mist = state.runs
    assert (drip.manifold, drip.line_id, drip.cause, drip.slot_id) == ("drip", "L01", IrrigationCause.PREWET, 5)
    assert (drip.start_epoch, drip.end_epoch) == (1793628000, 1793628120)
    assert (mist.manifold, mist.line_id, mist.cause, mist.slot_id) == ("mist", "L04", IrrigationCause.VPD, None)
    assert state.waiting == (
        IrrWaiting(line_id="L04", reason="occ"),
        IrrWaiting(line_id="L07", reason="man", until_epoch=1793628120),
    )
    assert state.faults == (
        IrrFault(line_id="L09", fault="backing_off", until_epoch=1793628600),
        IrrFault(line_id="L10", fault="mister_fault", until_epoch=1793689200),
    )
    assert state.vpd_source == VpdSources(center="house", south="peer", west="own")
    assert state.reject == "bad_hash"
    assert irr.parse_irr_state("") == IrrState()
    fallback = irr.parse_irr_state("m=3,fallback,2,1793646000,1793646360").runs[0]
    assert fallback.cause is IrrigationCause.FALLBACK and fallback.slot_id == 2


def test_state_tokens_in_any_order_format_canonically() -> None:
    shuffled = "j=bad_line v=ooo f=6,b,9 w=1,pau,7 m=6,schedule,1000,1,2 w=0,cap d=4,run_now,0,3,4"
    state = irr.parse_irr_state(shuffled)
    assert [run.line_id for run in state.runs] == ["L05", "L07"]  # drip first
    assert [item.line_id for item in state.waiting] == ["L02", "L01"]  # due order kept
    assert irr.format_irr_state(state) == (
        "d=4,run_now,0,3,4 m=6,schedule,1000,1,2 w=1,pau,7 w=0,cap f=6,b,9 v=ooo j=bad_line"
    )


@pytest.mark.parametrize(
    "text",
    [
        "x=1",
        "v",
        "=hoo",
        "D=0,schedule,4,1,2",
        "d=0,schedule,4,2,1",  # ends before it starts
        "d=0,unknown,4,1,2",  # unknown is the platform's cause
        "d=0,flood,4,1,2",
        "d=0,Schedule,4,1,2",
        "d=3,vpd,0,1,2",  # a mister on the drip manifold
        "m=0,schedule,4,1,2",  # a drip on the mist manifold
        "d=2,feed,0,1,2",  # L03 is never named
        "m=10,vpd,0,1,2",  # nor L11
        "d=11,schedule,4,1,2",
        "d=0,schedule,04,1,2",
        "d=0,schedule,65536,1,2",
        "d=0,schedule,4,1,4294967296",
        "d=0,schedule,4,-1,2",
        "d=0,schedule,4,1",
        "d=0,schedule,4,1,2,3",
        "d=0,schedule,4,1,2 d=1,feed,5,1,2",  # two drip runs
        "m=3,vpd,0,1,2 m=6,vpd,0,1,2",
        "w=0",
        "w=0,zzz",
        "w=0,man,1,2",
        "w=2,man",  # L03 takes no runs
        "w=0,man,",
        "f=0,b,1",  # faults are misters only
        "f=3,x,1",
        "f=3,b",
        "f=3,b,1 f=3,l,2",
        "v=hp",
        "v=hpox",
        "v=hpx",
        "v=HPO",
        "v=hoo v=hoo",
        "j=bad_crc",
        "j=",
        "j=bad_hash j=bad_slot",
        "v=hoo  j=bad_hash",
        " v=hoo",
        "v=hoo ",
        "v=hoo\tj=bad_hash",
    ],
)
def test_state_rejects_text_outside_the_grammar(text: str) -> None:
    with pytest.raises(ValueError):
        irr.parse_irr_state(text)


def test_state_models_reject_device_impossibilities() -> None:
    with pytest.raises(ValidationError):
        IrrRun(line_id="L01", cause="unknown", start_epoch=1, end_epoch=2)
    with pytest.raises(ValidationError):
        IrrState(runs=(irr.parse_irr_state("d=0,run_now,0,1,2").runs[0],) * 2)
    with pytest.raises(ValidationError):
        IrrFault(line_id="L05", fault="backing_off", until_epoch=1)
    with pytest.raises(ValidationError):
        ProgramId(hash16="7ae03efe564082b7", src="rom")


def _widest_state(waiting: int) -> IrrState:
    epoch = 4_294_967_295
    return IrrState(
        runs=(
            IrrRun(line_id="L06", cause="schedule", slot_id=65535, start_epoch=epoch, end_epoch=epoch),
            IrrRun(line_id="L10", cause="fallback", slot_id=65534, start_epoch=epoch, end_epoch=epoch),
        ),
        waiting=tuple(
            IrrWaiting(line_id=irr.LINE_IDS[sorted(irr.SCHEDULABLE_IDX)[n % 9]], reason="man", until_epoch=epoch - n)
            for n in range(waiting)
        ),
        faults=tuple(
            IrrFault(line_id=line_id, fault="mister_fault", until_epoch=epoch)
            for line_id in ("L04", "L07", "L08", "L09", "L10")
        ),
        vpd_source=VpdSources(center="house", south="peer", west="own"),
        reject="bad_header",
    )


def test_state_formatter_drops_waiting_tokens_latest_due_first() -> None:
    assert len(irr.format_irr_state(_widest_state(0))) < 200  # runs, faults, v and j always fit
    full = _widest_state(20)
    text = irr.format_irr_state(full)
    assert len(text) <= 255
    kept = irr.parse_irr_state(text)
    count = len(kept.waiting)
    assert 0 < count < len(full.waiting)
    assert kept.waiting == full.waiting[:count]  # a prefix: the latest due went first
    assert kept.runs == full.runs and kept.faults == full.faults
    assert (kept.vpd_source, kept.reject) == (full.vpd_source, full.reject)
    # The kept prefix fits as it is, and one more w= token would not: as many as fit are kept.
    assert irr.format_irr_state(full.model_copy(update={"waiting": full.waiting[:count]})) == text
    assert irr.format_irr_state(full.model_copy(update={"waiting": full.waiting[: count + 1]})) == text


def test_overflow_sample_was_formatted_from_its_source(vectors: dict) -> None:
    sample = next(s for s in vectors["text"]["irr_state"] if s["name"] == "overflow_drops_waiting")
    source = IrrState.model_validate(sample["formatted_from"])
    assert irr.format_irr_state(source) == sample["text"]
    parsed = irr.parse_irr_state(sample["text"])
    assert parsed.waiting == source.waiting[: len(parsed.waiting)]
    assert len(parsed.waiting) < len(source.waiting)


# ── irr_events and sequence order ────────────────────────────────────────────


def test_event_samples_round_trip_including_the_wrap(vectors: dict) -> None:
    samples = {sample["name"]: sample for sample in vectors["text"]["irr_events"]}
    for sample in samples.values():
        parsed = irr.parse_irr_events(sample["text"])
        assert [event.model_dump(mode="json") for event in parsed] == sample["parsed"], sample["name"]
        assert irr.format_irr_events(parsed) == sample["text"], sample["name"]
    wrap = irr.parse_irr_events(samples["wrap"]["text"])
    assert [event.seq for event in wrap] == [65534, 65535, 0, 1]
    assert wrap[1].slot_id is None and wrap[1].line_id == "L04"  # run-now
    assert len(samples["eight_full"]["text"]) <= 223
    assert len(irr.parse_irr_events(samples["eight_full"]["text"])) == 8


def test_events_parse_each_field() -> None:
    first, second = irr.parse_irr_events("101,0,4,20759,ok 102,7,0,20760,sh,rst")
    assert first == IrrEvent(seq=101, line_id="L01", slot_id=4, local_day=date(2026, 11, 2), outcome="ok")
    assert second == IrrEvent(
        seq=102, line_id="L08", slot_id=None, local_day=date(2026, 11, 3), outcome="short", reason="rst"
    )
    assert irr.parse_irr_events("") == []
    assert irr.parse_irr_events("0,9,65535,0,ca,cap")[0].local_day == date(1970, 1, 1)
    assert irr.parse_irr_events("1,0,1,65535,ok")[0].local_day == date(2149, 6, 6)


def test_eight_widest_events_fit_in_223_characters() -> None:
    widest = [
        IrrEvent(seq=65528 + n, line_id="L10", slot_id=65535, local_day=date(2149, 6, 6), outcome="ok", reason="cmd")
        for n in range(8)
    ]
    text = irr.format_irr_events(widest)
    assert len(text) <= 223
    assert irr.parse_irr_events(text) == widest


@pytest.mark.parametrize(
    "text",
    [
        " ".join(f"{n},0,4,20759,ok" for n in range(9)),  # more than 8
        "1,0,4,20759,unknown",
        "1,0,4,20759,ok,zzz",
        "1,0,4,20759,ok,",
        "1,0,4,20759",
        "1,0,4,20759,ok,cmd,x",
        "5,0,4,20759,ok 4,0,4,20759,ok",  # newest first
        "5,0,4,20759,ok 5,0,4,20759,ok",  # repeated sequence
        "0,0,4,20759,ok 40000,0,4,20759,ok",  # more than half the ring apart
        "1,2,0,20759,ok",  # L03 takes no runs
        "1,10,0,20759,ok",  # nor L11
        "1,11,0,20759,ok",
        "65536,0,4,20759,ok",
        "1,0,65536,20759,ok",
        "1,0,4,65536,ok",
        "01,0,4,20759,ok",
        "1,0,4,-1,ok",
        "1,0,4,20759,ok  2,0,4,20759,ok",
        " 1,0,4,20759,ok",
        "1,0,4,20759,ok ",
    ],
)
def test_events_reject_text_outside_the_grammar(text: str) -> None:
    with pytest.raises(ValueError):
        irr.parse_irr_events(text)


def test_events_formatter_rejects_what_the_parser_rejects() -> None:
    event = IrrEvent(seq=1, line_id="L01", slot_id=4, local_day=date(2026, 11, 2), outcome="ok")
    with pytest.raises(ValueError):
        irr.format_irr_events([event.model_copy(update={"seq": n}) for n in range(9)])
    with pytest.raises(ValueError):
        irr.format_irr_events([event.model_copy(update={"seq": 2}), event])
    with pytest.raises(ValidationError):
        IrrEvent(seq=1, line_id="L11", local_day=date(2026, 11, 2), outcome="ok")


@pytest.mark.parametrize(
    ("a", "b", "newer"),
    [
        (1, 0, True),
        (0, 1, False),
        (0, 65535, True),
        (65535, 0, False),
        (5, 5, False),
        (5 + 32767, 5, True),
        (5 + 32768, 5, False),
        (3, 65530, True),
    ],
)
def test_seq_newer_uses_u16_serial_arithmetic(a: int, b: int, newer: bool) -> None:
    assert irr.seq_newer(a, b) is newer


def test_seq_newer_rejects_non_u16() -> None:
    for a, b in ((65536, 0), (-1, 0), (0, True), (1.0, 0)):
        with pytest.raises(ValueError):
            irr.seq_newer(a, b)  # type: ignore[arg-type]


# ── Module boundaries ────────────────────────────────────────────────────────


def test_module_is_pure_and_import_light() -> None:
    """Nothing from ingestor/ (or any other repo package): the api, MCP and ingestor images all load it.

    The schedule model (VD-37, #1152) adds its sibling modules only: the shared solar code, the
    snap rule and the registry's site rows.
    """
    imported: set[str] = set()
    siblings: set[str] = set()
    for node in ast.walk(ast.parse(MODULE_PATH.read_text())):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module, "no relative imports"
            imported.add(node.module.split(".")[0])
            if node.module.startswith("verdify_schemas"):
                siblings.add(node.module)
    stdlib = {
        "__future__",
        "collections",
        "datetime",
        "enum",
        "functools",
        "hashlib",
        "itertools",
        "math",
        "re",
        "struct",
        "types",
        "typing",
        "zoneinfo",
    }
    assert imported <= stdlib | {"pydantic", "verdify_schemas"}
    assert siblings == {"verdify_schemas.setting_values", "verdify_schemas.solar", "verdify_schemas.tunable_registry"}


REEXPORTED = (
    "DEVICE_IDX",
    "MISTER_MASK_BIT",
    "REASON_WORDS",
    "SCHEDULABLE_IDX",
    "START_KIND",
    "IrrEvent",
    "IrrFault",
    "IrrRun",
    "IrrState",
    "IrrWaiting",
    "IrrigationCause",
    "ProgramId",
    "ProgramRecord",
    "ProgramRecordError",
    "RecordLine",
    "RecordSlot",
    "RunOutcome",
    "VpdSources",
    "format_irr_events",
    "format_irr_program_id",
    "format_irr_state",
    "pack_program",
    "parse_irr_events",
    "parse_irr_program_id",
    "parse_irr_state",
    "program_chunks",
    "program_hash16",
    "program_sha256",
    "seq_newer",
    "unpack_program",
)


def test_package_reexports_the_new_names_and_keeps_the_retired_ones() -> None:
    for name in REEXPORTED:
        assert getattr(verdify_schemas, name) is getattr(irr, name), name
        assert name in verdify_schemas.__all__, name
        assert name in irr.__all__, name
    assert set(irr.__all__) <= set(dir(irr))
    assert verdify_schemas.IrrigationSchedule is operations.IrrigationSchedule
    assert verdify_schemas.IrrigationLog is operations.IrrigationLog
    assert not hasattr(irr, "IrrigationSchedule") and not hasattr(irr, "IrrigationLog")
