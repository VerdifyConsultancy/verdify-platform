#!/usr/bin/env python3
"""Generate verdify_schemas/tests/fixtures/irrigation_program_vectors.json (VD-36, #1151).

The golden vectors for the irrigation program record VIP1 (verdify_schemas/irrigation.py) and the
controller's irr_program_id, irr_state and irr_events text sensors. One file feeds both test
suites: verdify_schemas/tests/test_irrigation_program.py here, and the firmware decoder's native
tests (#1159 extends this script to render the C include they read). The compiler (#1152) must
reproduce the seed vector byte for byte, and M3's seed rows (#1153) equal its program.

Three programs:

- empty: site values 360, 90 and 15; every mask 0; caps 0; no slots.
- seed:  the program M3 seeds. L01 enabled, cap 60; L04 enabled, cap 120, reserve 60, VPD member
         and not a schedule member; L05 cap 30; L07 and L09 cap 45, reserve 15; slots 1-3 on L04,
         every day, solar noon -120, 0 and +120, on_s 45, count 4, gap_s 60, degraded_only.
- full:  24 slots on every schedulable line, every start kind, negative offsets, slot_id 65535
         last, each line kind's largest values, degraded_only only on L04, L07 and L09, every line
         enabled and paused, both mister masks 0x07, and the header at its maxima.

Every slot stays inside its line kind's bounds (irrigation §4.3), which the firmware decoder
checks too. Each text sample carries its parsed form. Never edit the JSON by hand: change the
inputs below and regenerate. Run with --check to verify the committed fixture matches
regeneration (scripts/ci-local.sh does).

It also renders firmware/test/irrigation_program_vectors_generated.inc (VD-44, #1159) from the
JSON: the vector bytes, hashes and decoded programs, the text samples, the fixed codes of
verdify_schemas/irrigation.py (line kinds, clean partners and BOUNDS_BY_KIND included), and
REJECT_CASES, golden vectors with bytes patched and the code unpack_program() gives each, so the
C++ decoder is checked against the Python one. --check covers the include too; never edit it by
hand.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas import irrigation as irr  # noqa: E402
from verdify_schemas.irrigation import (  # noqa: E402
    DEVICE_IDX,
    START_KIND,
    IrrFault,
    IrrRun,
    IrrState,
    IrrWaiting,
    ProgramId,
    ProgramRecord,
    RecordLine,
    RecordSlot,
    VpdSources,
)

FIXTURE_PATH = REPO_ROOT / "verdify_schemas" / "tests" / "fixtures" / "irrigation_program_vectors.json"
INC_PATH = REPO_ROOT / "firmware" / "test" / "irrigation_program_vectors_generated.inc"

MON, TUE, WED, THU, FRI, SAT, SUN = (1 << bit for bit in range(7))
EVERY_DAY = 0x7F
WEEKDAYS = MON | TUE | WED | THU | FRI
CLOCK, SUNRISE, SOLAR_NOON, SUNSET = (START_KIND[kind] for kind in ("clock", "sunrise", "solar_noon", "sunset"))

# Reference instants for the samples: 2026-11-02 (America/Denver, MST, UTC-7).
DAY_0000 = 1793602800  # 2026-11-02 00:00 MST
DAY_0600 = DAY_0000 + 6 * 3600
DAY_0700 = DAY_0000 + 7 * 3600
DAY_1200 = DAY_0000 + 12 * 3600
NEXT_MIDNIGHT = DAY_0000 + 24 * 3600
LOCAL_DAY = 20759  # 2026-11-02 as America/Denver days since 1970-01-01


def _lines(**by_line: RecordLine) -> tuple[RecordLine, ...]:
    return tuple(by_line.get(line_id, RecordLine()) for line_id in irr.LINE_IDS)


def _mask(*line_ids: str) -> int:
    return sum(1 << DEVICE_IDX[line_id] for line_id in line_ids)


def _slot(slot_id: int, line_id: str, days: int, kind: int, start: int, on_s: int, **extra: int | bool) -> RecordSlot:
    return RecordSlot(
        slot_id=slot_id, line=DEVICE_IDX[line_id], weekday_mask=days, start_kind=kind, start=start, on_s=on_s, **extra
    )


EMPTY = ProgramRecord(saf4_gal=360, rh_ceiling_pct=90, climate_yield_min=15, lines=_lines())

SEED = ProgramRecord(
    saf4_gal=360,
    rh_ceiling_pct=90,
    climate_yield_min=15,
    enabled_mask=_mask("L01", "L04"),
    vpd_member_mask=1 << irr.MISTER_MASK_BIT["L04"],
    schedule_member_mask=0,
    lines=_lines(
        L01=RecordLine(daily_cap_min=60),
        L04=RecordLine(daily_cap_min=120, vpd_reserve_min=60),
        L05=RecordLine(daily_cap_min=30),
        L07=RecordLine(daily_cap_min=45, vpd_reserve_min=15),
        L09=RecordLine(daily_cap_min=45, vpd_reserve_min=15),
    ),
    slots=tuple(
        _slot(slot_id, "L04", EVERY_DAY, SOLAR_NOON, offset, 45, count=4, gap_s=60, degraded_only=True)
        for slot_id, offset in ((1, -120), (2, 0), (3, 120))
    ),
)

# Every line paused, an hour apart, and L04 at the top of the u32 range.
_PAUSED = {line_id: DAY_0000 + 3600 * idx for idx, line_id in enumerate(irr.LINE_IDS)} | {"L04": 0xFFFF_FFFF}
_FULL_CAPS = {
    "L01": (480, 0),
    "L02": (240, 0),
    "L03": (1, 0),
    "L04": (480, 480),
    "L05": (30, 0),
    "L06": (60, 0),
    "L07": (45, 15),
    "L08": (90, 0),
    "L09": (120, 60),
    "L10": (75, 0),
    "L11": (0, 0),
}
FULL = ProgramRecord(
    saf4_gal=600,
    rh_ceiling_pct=98,
    climate_yield_min=30,
    enabled_mask=0x7FF,
    vpd_member_mask=0x07,
    schedule_member_mask=0x07,
    lines=tuple(
        RecordLine(daily_cap_min=cap, vpd_reserve_min=reserve, paused_until=_PAUSED[line_id])
        for line_id, (cap, reserve) in _FULL_CAPS.items()
    ),
    slots=(
        _slot(1, "L01", WEEKDAYS, CLOCK, 0, 7200),
        _slot(2, "L04", EVERY_DAY, SUNRISE, -240, 90, count=60, gap_s=600),
        _slot(3, "L02", SUN, SUNRISE, 60, 7200, prewet_s=1800, flush_s=1800),
        _slot(7, "L07", MON | WED | FRI | SUN, SOLAR_NOON, -120, 45, count=4, gap_s=60, degraded_only=True),
        _slot(10, "L09", EVERY_DAY, SOLAR_NOON, 0, 45, count=4, gap_s=60, degraded_only=True),
        _slot(42, "L08", WED, SUNSET, -90, 90, count=60, gap_s=600, prewet_s=1800, flush_s=1800),
        _slot(100, "L05", EVERY_DAY, CLOCK, 1439, 10),
        _slot(255, "L06", SAT, SUNRISE, 240, 600, flush_s=1800),
        _slot(256, "L10", TUE, SOLAR_NOON, 30, 10, gap_s=30, prewet_s=300),
        _slot(300, "L04", EVERY_DAY, SOLAR_NOON, -120, 45, count=4, gap_s=60, degraded_only=True),
        _slot(301, "L01", SAT | SUN, SUNRISE, -30, 1800),
        _slot(1000, "L07", EVERY_DAY, SUNRISE, -60, 30, count=10, gap_s=120),
        _slot(1001, "L09", WEEKDAYS, CLOCK, 480, 60, count=20, gap_s=300),
        _slot(2048, "L04", MON, SUNSET, 0, 20, count=2, gap_s=30),
        _slot(4097, "L02", THU, CLOCK, 390, 300, prewet_s=120, flush_s=240),
        _slot(9999, "L05", FRI, SUNRISE, 15, 3600),
        _slot(12345, "L06", MON, SOLAR_NOON, -240, 7200, prewet_s=1800, flush_s=1800),
        _slot(20000, "L08", SUN, CLOCK, 1080, 60, count=5, gap_s=90, prewet_s=600, flush_s=600),
        _slot(30000, "L10", THU, SUNRISE, -120, 90, count=60, gap_s=600, prewet_s=1800, flush_s=1800),
        _slot(32767, "L01", WED, SUNSET, -45, 900),
        _slot(32768, "L07", EVERY_DAY, SOLAR_NOON, 240, 90, count=60, gap_s=600),
        _slot(40000, "L09", SAT, SUNSET, -240, 10, gap_s=30, degraded_only=True),
        _slot(65534, "L04", TUE, CLOCK, 1, 90, gap_s=600),
        _slot(65535, "L01", EVERY_DAY, SOLAR_NOON, 120, 120),
    ),
)

PROGRAMS = (("empty", EMPTY), ("seed", SEED), ("full", FULL))

# irr_state samples as the controller publishes them.
STATE_SAMPLES = (
    ("empty", ""),
    ("idle", "v=hoo"),
    ("drip_schedule", f"d=0,schedule,4,{DAY_0700},{DAY_0700 + 600} v=hoo"),
    # The seed's slot 2 at solar noon while VPD sensing is degraded: 4 pulses of 45 s, 60 s apart.
    ("fallback_mist", f"m=3,fallback,2,{DAY_1200},{DAY_1200 + 360} v=hph"),
    # An L02 feed (slot 5) in its prewet on L01; L04 blocked by occupancy; L07 waiting for the
    # manifold; L09 backing off.
    (
        "feed_prewet_waiting",
        f"d=0,prewet,5,{DAY_0700},{DAY_0700 + 120} w=3,occ w=6,man,{DAY_0700 + 120} f=8,b,{DAY_0700 + 600} v=hpo",
    ),
    ("run_now_feed", f"d=1,feed,0,{DAY_0700 + 3600},{DAY_0700 + 3900} w=0,man v=hoo"),
    ("vpd_pulse_rejected", f"m=3,vpd,0,{DAY_0700 + 3620},{DAY_0700 + 3665} v=hoo j=bad_hash"),
    (
        "faults",
        f"f=6,l,{NEXT_MIDNIGHT} f=7,l,{NEXT_MIDNIGHT} f=3,b,{DAY_0700 + 1800} v=hpo j=bad_slot",
    ),
)

# A state too long for 255 characters: formatting drops the latest-due w= tokens. It is one the engine
# can publish, at 07:15: L01's 30 min scheduled drip paused after 15 min for one 45 s VPD pulse on L04
# (§4.7: the only way a drip and a mist run share a moment; its planned end moves by the pause); the
# other drips waiting for the manifold; L07 and L09, VPD members, backing off; and L08 and L10, the
# fertilizer misters, latched until midnight (F10: they latch on their first fault, never back off).
DRIP_END = DAY_0700 + 1800 + 45
OVERFLOW_STATE = IrrState(
    runs=(
        IrrRun(line_id="L01", cause="schedule", slot_id=65535, start_epoch=DAY_0700, end_epoch=DRIP_END),
        IrrRun(line_id="L04", cause="vpd", slot_id=None, start_epoch=DAY_0700 + 900, end_epoch=DAY_0700 + 945),
    ),
    waiting=(
        IrrWaiting(line_id="L02", reason="man", until_epoch=DRIP_END),
        IrrWaiting(line_id="L05", reason="man", until_epoch=DRIP_END),
        IrrWaiting(line_id="L06", reason="man", until_epoch=DRIP_END),
        IrrWaiting(line_id="L07", reason="bof", until_epoch=DAY_0700 + 1200),
        IrrWaiting(line_id="L08", reason="flt", until_epoch=NEXT_MIDNIGHT),
        IrrWaiting(line_id="L09", reason="bof", until_epoch=DAY_0700 + 2700),
        IrrWaiting(line_id="L10", reason="flt", until_epoch=NEXT_MIDNIGHT),
    ),
    faults=(
        IrrFault(line_id="L07", fault="backing_off", until_epoch=DAY_0700 + 1200),
        IrrFault(line_id="L08", fault="mister_fault", until_epoch=NEXT_MIDNIGHT),
        IrrFault(line_id="L09", fault="backing_off", until_epoch=DAY_0700 + 2700),
        IrrFault(line_id="L10", fault="mister_fault", until_epoch=NEXT_MIDNIGHT),
    ),
    vpd_source=VpdSources(center="house", south="peer", west="own"),
    reject="bad_header",
)

EVENT_SAMPLES = (
    ("none", ""),
    ("today", f"101,0,4,{LOCAL_DAY},ok 102,3,2,{LOCAL_DAY},sk,vok 103,1,5,{LOCAL_DAY},sh,rst"),
    (
        "wrap",
        f"65534,0,4,{LOCAL_DAY - 1},ok 65535,3,0,{LOCAL_DAY - 1},st,cmd 0,6,1000,{LOCAL_DAY},mi,clk "
        f"1,8,10,{LOCAL_DAY},ca,cap",
    ),
    (
        "eight_full",
        " ".join(
            f"{seq},{idx},{slot},{LOCAL_DAY},{code},{reason}"
            for seq, idx, slot, code, reason in (
                (65528, 9, 30000, "sk", "pau"),
                (65529, 8, 40000, "sk", "nvm"),
                (65530, 7, 20000, "sk", "occ"),
                (65531, 6, 32768, "sh", "res"),
                (65532, 5, 12345, "st", "cmd"),
                (65533, 4, 10000, "mi", "man"),
                (65534, 3, 65534, "pe", "bud"),
                (65535, 0, 65535, "ca", "cap"),
            )
        ),
    ),
)


def _u8(value: int) -> bytes:
    return struct.pack("<B", value)


def _u16(value: int) -> bytes:
    return struct.pack("<H", value)


def _i16(value: int) -> bytes:
    return struct.pack("<h", value)


_LINE = irr.PROGRAM_LINES_OFFSET  # line i at _LINE + 8 * i: cap +0, reserve +2, paused_until +4
_SLOT = irr.PROGRAM_SLOTS_OFFSET  # slot i at _SLOT + 20 * i: id +0, line +2, days +3, kind +4, flags +5,
#                                   start +6, on_s +8, gap_s +10, prewet_s +12, flush_s +14, count +16

# Golden vectors with one or two byte spans patched, and the code unpack_program() must give each
# (checked here at generation time). The firmware decoder (#1159) must agree on every one.
REJECT_CASES: tuple[tuple[str, str, tuple[tuple[int, bytes], ...], str], ...] = (
    ("magic", "seed", ((0, b"X"),), "bad_magic"),
    ("magic_version_2", "seed", ((3, b"2"),), "bad_magic"),
    ("header_reserved_13", "seed", ((13, _u8(1)),), "bad_header"),
    ("header_reserved_15", "empty", ((15, _u8(0x80)),), "bad_header"),
    ("slot_count_25", "empty", ((12, _u8(25)),), "bad_header"),
    ("saf4_99", "seed", ((4, _u16(99)),), "bad_header"),
    ("saf4_601", "seed", ((4, _u16(601)),), "bad_header"),
    ("rh_ceiling_74", "seed", ((6, _u8(74)),), "bad_header"),
    ("rh_ceiling_99", "seed", ((6, _u8(99)),), "bad_header"),
    ("climate_yield_4", "seed", ((7, _u8(4)),), "bad_header"),
    ("climate_yield_31", "seed", ((7, _u8(31)),), "bad_header"),
    ("enabled_mask_bit_11", "seed", ((8, _u16(0x0809)),), "bad_header"),
    ("vpd_member_mask_bit_3", "seed", ((10, _u8(0x09)),), "bad_header"),
    ("schedule_member_mask_bit_3", "seed", ((11, _u8(0x08)),), "bad_header"),
    ("line_cap_481", "seed", ((_LINE, _u16(481)),), "bad_line"),
    ("line_reserve_over_cap", "seed", ((_LINE + 8 * 3 + 2, _u16(121)),), "bad_line"),
    ("line_reserve_without_cap", "empty", ((_LINE + 8 * 10 + 2, _u16(1)),), "bad_line"),
    ("unused_slot_not_zero", "seed", ((_SLOT + 20 * 3 + 19, _u8(1)),), "bad_slot"),
    ("slot_reserved_byte", "seed", ((_SLOT + 18, _u8(1)),), "bad_slot"),
    ("slot_flag_bit_1", "seed", ((_SLOT + 5, _u8(0x03)),), "bad_slot"),
    ("slot_id_0", "seed", ((_SLOT, _u16(0)),), "bad_slot"),
    ("slot_id_repeated", "seed", ((_SLOT + 20, _u16(1)),), "bad_slot"),
    ("slot_id_descending", "full", ((_SLOT + 20 * 23, _u16(65533)),), "bad_slot"),
    ("slot_on_l03", "full", ((_SLOT + 2, _u8(2)),), "bad_slot"),
    ("slot_on_l11", "full", ((_SLOT + 2, _u8(10)),), "bad_slot"),
    ("slot_line_11", "full", ((_SLOT + 2, _u8(11)),), "bad_slot"),
    ("weekday_mask_0", "seed", ((_SLOT + 3, _u8(0)),), "bad_slot"),
    ("weekday_mask_bit_7", "seed", ((_SLOT + 3, _u8(0xFF)),), "bad_slot"),
    ("start_kind_4", "seed", ((_SLOT + 4, _u8(4)),), "bad_slot"),
    ("clock_start_1440", "full", ((_SLOT + 6, _i16(1440)),), "bad_slot"),
    ("clock_start_minus_1", "full", ((_SLOT + 6, _i16(-1)),), "bad_slot"),
    ("solar_offset_241", "seed", ((_SLOT + 6, _i16(241)),), "bad_slot"),
    ("solar_offset_minus_241", "seed", ((_SLOT + 6, _i16(-241)),), "bad_slot"),
    ("degraded_only_on_l01", "full", ((_SLOT + 5, _u8(1)),), "bad_slot"),
    ("on_s_9", "seed", ((_SLOT + 8, _u16(9)),), "bad_slot"),
    ("on_s_7201", "full", ((_SLOT + 8, _u16(7201)),), "bad_slot"),
    ("gap_s_601", "seed", ((_SLOT + 10, _u16(601)),), "bad_slot"),
    ("prewet_s_1801", "full", ((_SLOT + 20 * 2 + 12, _u16(1801)),), "bad_slot"),
    ("flush_s_1801", "full", ((_SLOT + 20 * 2 + 14, _u16(1801)),), "bad_slot"),
    ("count_0", "seed", ((_SLOT + 16, _u8(0)),), "bad_slot"),
    ("count_61", "seed", ((_SLOT + 16, _u8(61)),), "bad_slot"),
    ("magic_before_header", "seed", ((0, b"X"), (4, _u16(99))), "bad_magic"),
    ("header_before_slot", "seed", ((4, _u16(99)), (_SLOT + 16, _u8(0))), "bad_header"),
    ("line_before_slot", "seed", ((_LINE, _u16(481)), (_SLOT + 16, _u8(0))), "bad_line"),
)


def _fail(message: str) -> None:
    raise SystemExit(f"gen-irrigation-program-vectors: {message}")


def _state_sample(name: str, text: str) -> dict:
    parsed = irr.parse_irr_state(text)
    if irr.format_irr_state(parsed) != text:
        _fail(f"irr_state sample {name!r} does not round-trip")
    return {"name": name, "text": text, "parsed": parsed.model_dump(mode="json")}


def _event_sample(name: str, text: str) -> dict:
    parsed = irr.parse_irr_events(text)
    if irr.format_irr_events(parsed) != text:
        _fail(f"irr_events sample {name!r} does not round-trip")
    return {"name": name, "text": text, "parsed": [event.model_dump(mode="json") for event in parsed]}


def _program_id_sample(name: str, program_id: ProgramId) -> dict:
    text = irr.format_irr_program_id(program_id)
    if irr.parse_irr_program_id(text) != program_id:
        _fail(f"irr_program_id sample {name!r} does not round-trip")
    return {"name": name, "text": text, "parsed": program_id.model_dump(mode="json")}


def build_fixture() -> dict:
    vectors = []
    hash16: dict[str, str] = {}
    for name, program in PROGRAMS:
        data = irr.pack_program(program)
        if len(data) != irr.PROGRAM_RECORD_LEN or irr.unpack_program(data) != program:
            _fail(f"program {name!r} does not round-trip through pack_program/unpack_program")
        hash16[name] = irr.program_hash16(data)
        vectors.append(
            {
                "name": name,
                "program": program.model_dump(mode="json"),
                "hex": data.hex(),
                "sha256": irr.program_sha256(data),
                "chunks": [[offset, chunk] for offset, chunk in irr.program_chunks(data)],
            }
        )

    overflow_text = irr.format_irr_state(OVERFLOW_STATE)
    overflow = _state_sample("overflow_drops_waiting", overflow_text)
    if len(overflow_text) > irr.IRR_TEXT_MAX_LEN or len(overflow["parsed"]["waiting"]) >= len(OVERFLOW_STATE.waiting):
        _fail("the overflow sample must fit 255 characters by dropping w= tokens")
    overflow["formatted_from"] = OVERFLOW_STATE.model_dump(mode="json")

    return {
        "_comment": (
            "Golden vectors for the irrigation program record VIP1 and the irr_program_id, irr_state and "
            "irr_events grammars (VD-36, #1151; verdify_schemas/irrigation.py). Generated by "
            "scripts/gen-irrigation-program-vectors.py - DO NOT EDIT BY HAND. Consumed by "
            "verdify_schemas/tests/test_irrigation_program.py and by the firmware decoder's native tests "
            "(#1159). Each vector's program is the ProgramRecord input, hex its 584 B record, sha256 the hash "
            "over all of it and chunks the (offset, hex) pairs for irr_program_chunk. Each text sample "
            "carries its parsed form; overflow_drops_waiting also carries the state it was formatted from."
        ),
        "record_len": irr.PROGRAM_RECORD_LEN,
        "chunk_max": irr.PROGRAM_CHUNK_MAX,
        "vectors": vectors,
        "text": {
            "irr_program_id": [
                _program_id_sample("seed_nvs", ProgramId(hash16=hash16["seed"], src="nvs")),
                _program_id_sample("full_nvs", ProgramId(hash16=hash16["full"], src="nvs")),
                _program_id_sample("rom_default", ProgramId(hash16=irr.ROM_HASH16, src="rom")),
            ],
            "irr_state": [_state_sample(name, text) for name, text in STATE_SAMPLES] + [overflow],
            "irr_events": [_event_sample(name, text) for name, text in EVENT_SAMPLES],
        },
    }


# ── The firmware include (VD-44, #1159) ──────────────────────────────────────


def _c_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _byte_lines(data: bytes, indent: str, per_line: int = 16) -> list[str]:
    return [
        indent + ", ".join(f"0x{b:02x}" for b in data[start : start + per_line]) + ","
        for start in range(0, len(data), per_line)
    ]


def _mask(indexes: frozenset[int]) -> str:
    return f"0x{sum(1 << i for i in indexes):04x}"


def _reject_rows(by_name: dict[str, bytes]) -> list[str]:
    rows = []
    for name, base, patches, expected in REJECT_CASES:
        if not 1 <= len(patches) <= 2 or any(not 1 <= len(span) <= 4 for _, span in patches):
            _fail(f"reject case {name!r}: one or two patches of 1-4 bytes")
        data = bytearray(by_name[base])
        for offset, span in patches:
            data[offset : offset + len(span)] = span
        if bytes(data) == by_name[base]:
            _fail(f"reject case {name!r} does not change its {base} vector")
        try:
            irr.unpack_program(bytes(data))
        except irr.ProgramRecordError as exc:
            if exc.code != expected:
                _fail(f"reject case {name!r}: unpack_program() says {exc.code}, expected {expected}")
        else:
            _fail(f"reject case {name!r}: unpack_program() accepted it")
        base_index = [vector_name for vector_name, _ in PROGRAMS].index(base)
        offsets = ", ".join(str(offset) for offset, _ in patches)
        lens = ", ".join(str(len(span)) for _, span in patches)
        spans = ", ".join("{" + ", ".join(f"0x{b:02x}" for b in span) + "}" for _, span in patches)
        rows.append(
            f"    {{{_c_string(name)}, {base_index}, {len(patches)}, {{{offsets}}}, {{{lens}}}, {{{spans}}}, "
            f"{_c_string(expected)}}},"
        )
    return rows


def render_include(fixture: dict) -> str:
    """The C++ include for firmware/test/test_irrigation_program.cpp, rendered from the fixture JSON."""
    vectors = fixture["vectors"]
    by_name = {vector["name"]: bytes.fromhex(vector["hex"]) for vector in vectors}
    out: list[str] = [
        "// AUTOGENERATED by scripts/gen-irrigation-program-vectors.py - DO NOT EDIT.",
        "// Rendered from verdify_schemas/tests/fixtures/irrigation_program_vectors.json and the fixed",
        "// codes of verdify_schemas/irrigation.py, for firmware/test/test_irrigation_program.cpp (VD-44).",
        "// Regenerate with: python3 scripts/gen-irrigation-program-vectors.py",
        "",
        "// ── Fixed codes (verdify_schemas/irrigation.py) ──",
        f"static const size_t kPyRecordLen = {fixture['record_len']};",
        f"static const size_t kPyChunkMax = {fixture['chunk_max']};",
        f"static const size_t kPyLinesOffset = {irr.PROGRAM_LINES_OFFSET};",
        f"static const size_t kPySlotsOffset = {irr.PROGRAM_SLOTS_OFFSET};",
        f"static const size_t kPyMaxSlots = {irr.PROGRAM_MAX_SLOTS};",
        f"static const size_t kPyLineCount = {irr.PROGRAM_LINE_COUNT};",
        f"static const char kPyMagic[] = {_c_string(irr.PROGRAM_MAGIC.decode())};",
        f"static const uint8_t kPySlotFlagDegradedOnly = {irr.SLOT_FLAG_DEGRADED_ONLY};",
        "static const char* const kPyLineIds[] = {" + ", ".join(_c_string(x) for x in irr.LINE_IDS) + "};",
        "// MISTER_MASK_BIT as {device_idx, bit}.",
        "static const uint8_t kPyMisterMaskBit[][2] = {"
        + ", ".join(f"{{{irr.DEVICE_IDX[line_id]}, {bit}}}" for line_id, bit in irr.MISTER_MASK_BIT.items())
        + "};",
        "static const char* const kPyStartKinds[] = {"
        + ", ".join(_c_string(kind) for kind, _ in sorted(irr.START_KIND.items(), key=lambda item: item[1]))
        + "};",
        "static const int16_t kPyStartRange[][2] = {"
        + ", ".join(f"{{{lo}, {hi}}}" for _, (lo, hi) in sorted(irr.START_RANGE.items()))
        + "};",
        f"static const uint16_t kPySchedulableMask = {_mask(irr.SCHEDULABLE_IDX)};",
        f"static const uint16_t kPyDripManifoldMask = {_mask(irr.DRIP_MANIFOLD_IDX)};",
        f"static const uint16_t kPyMistManifoldMask = {_mask(irr.MIST_MANIFOLD_IDX)};",
        f"static const uint16_t kPyDegradedOnlyMask = {_mask(irr.DEGRADED_ONLY_IDX)};",
        "static const char* const kPyCauses[] = {"
        + ", ".join(_c_string(cause.value) for cause in irr.IrrigationCause if cause is not irr.IrrigationCause.UNKNOWN)
        + "};",
        "// OUTCOME_CODES, in RunOutcome order.",
        "static const char* const kPyOutcomeCodes[] = {"
        + ", ".join(
            _c_string(code)
            for outcome in irr.RunOutcome
            for code, value in irr.OUTCOME_CODES.items()
            if value is outcome
        )
        + "};",
        "static const char* const kPyReasonCodes[] = {" + ", ".join(_c_string(x) for x in irr.REASON_WORDS) + "};",
        "static const char* const kPyRejectCodes[] = {" + ", ".join(_c_string(x) for x in irr.REJECT_CODES) + "};",
        f"static const char kPyRomHash16[] = {_c_string(irr.ROM_HASH16)};",
        f"static const size_t kPyEventsMax = {irr.IRR_EVENTS_MAX};",
        f"static const size_t kPyTextMaxLen = {irr.IRR_TEXT_MAX_LEN};",
        "// LINE_KIND and CLEAN_PARTNER by device_idx (255: none).",
        "static const char* const kPyLineKind[] = {"
        + ", ".join(_c_string(irr.LINE_KIND[x]) for x in irr.LINE_IDS)
        + "};",
        "static const uint8_t kPyCleanPartner[] = {"
        + ", ".join(
            str(irr.DEVICE_IDX[irr.CLEAN_PARTNER[x]]) if x in irr.CLEAN_PARTNER else "255" for x in irr.LINE_IDS
        )
        + "};",
        "// BOUNDS_BY_KIND, inclusive {lo, hi} per field: on_s, count, gap_s, prewet_s, flush_s.",
        "struct PyKindBounds { const char* kind; uint16_t field[5][2]; };",
        "static const PyKindBounds kPyKindBounds[] = {",
        *(
            f"    {{{_c_string(kind)}, {{"
            + ", ".join(
                f"{{{bounds[name][0]}, {bounds[name][1]}}}"
                for name in ("on_s", "count", "gap_s", "prewet_s", "flush_s")
            )
            + "}},"
            for kind, bounds in irr.BOUNDS_BY_KIND.items()
        ),
        "};",
        "",
        "// ── Golden vectors ──",
        f"static const size_t kGoldenVectorCount = {len(vectors)};",
        "static const char* const kGoldenVectorNames[] = {" + ", ".join(_c_string(v["name"]) for v in vectors) + "};",
        f"static const uint8_t kGoldenVectorBytes[][{fixture['record_len']}] = {{",
    ]
    for vector in vectors:
        out.append(f"    {{  // {vector['name']}")
        out.extend(_byte_lines(bytes.fromhex(vector["hex"]), "        "))
        out.append("    },")
    out.append("};")
    out.append("static const uint8_t kGoldenVectorSha256[][32] = {")
    for vector in vectors:
        out.append(f"    {{  // {vector['name']}")
        out.extend(_byte_lines(bytes.fromhex(vector["sha256"]), "        "))
        out.append("    },")
    out.append("};")
    chunk_count = max(len(vector["chunks"]) for vector in vectors)
    out.append(f"static const size_t kGoldenChunkCount = {chunk_count};")
    out.append("// irr_program_chunk (offset, length) pairs.")
    out.append(f"static const uint16_t kGoldenChunks[][{chunk_count}][2] = {{")
    for vector in vectors:
        pairs = ", ".join(f"{{{offset}, {len(chunk) // 2}}}" for offset, chunk in vector["chunks"])
        out.append(f"    {{{pairs}}},  // {vector['name']}")
    out.append("};")
    out.extend(
        [
            "struct GoldenLine { uint16_t daily_cap_min; uint16_t vpd_reserve_min; uint32_t paused_until; };",
            "struct GoldenSlot {",
            "  uint16_t slot_id; uint8_t line; uint8_t weekday_mask; uint8_t start_kind; bool degraded_only;",
            "  int16_t start; uint16_t on_s; uint16_t gap_s; uint16_t prewet_s; uint16_t flush_s; uint8_t count;",
            "};",
            "struct GoldenProgram {",
            "  uint16_t saf4_gal; uint8_t rh_ceiling_pct; uint8_t climate_yield_min; uint16_t enabled_mask;",
            "  uint8_t vpd_member_mask; uint8_t schedule_member_mask; uint8_t slot_count;",
            f"  GoldenLine lines[{irr.PROGRAM_LINE_COUNT}];",
            f"  GoldenSlot slots[{irr.PROGRAM_MAX_SLOTS}];",
            "};",
            "// Each vector's ProgramRecord, as the decoder must return it.",
            "static const GoldenProgram kGoldenPrograms[] = {",
        ]
    )
    for vector in vectors:
        program = vector["program"]
        out.append(f"    {{  // {vector['name']}")
        out.append(
            f"        {program['saf4_gal']}, {program['rh_ceiling_pct']}, {program['climate_yield_min']}, "
            f"0x{program['enabled_mask']:04x}, 0x{program['vpd_member_mask']:02x}, "
            f"0x{program['schedule_member_mask']:02x}, {len(program['slots'])},"
        )
        lines = ", ".join(
            f"{{{line['daily_cap_min']}, {line['vpd_reserve_min']}, {line['paused_until']}u}}"
            for line in program["lines"]
        )
        out.append(f"        {{{lines}}},")
        out.append("        {")
        for slot in program["slots"]:
            out.append(
                f"            {{{slot['slot_id']}, {slot['line']}, 0x{slot['weekday_mask']:02x}, {slot['start_kind']}, "
                f"{'true' if slot['degraded_only'] else 'false'}, {slot['start']}, {slot['on_s']}, {slot['gap_s']}, "
                f"{slot['prewet_s']}, {slot['flush_s']}, {slot['count']}}},"
            )
        out.append("        },")
        out.append("    },")
    out.append("};")
    out.extend(
        [
            "",
            "// ── Reject cases: a golden vector, one or two byte spans patched, and unpack_program()'s code ──",
            "struct RejectCase {",
            "  const char* name; uint8_t base; uint8_t patch_count; uint16_t offset[2]; uint8_t len[2];",
            "  uint8_t bytes[2][4]; const char* code;",
            "};",
            "static const RejectCase kRejectCases[] = {",
        ]
    )
    out.extend(_reject_rows(by_name))
    out.append("};")
    out.append(f"static const size_t kRejectCaseCount = {len(REJECT_CASES)};")

    text = fixture["text"]
    out.extend(
        ["", "// ── Text samples (#1151's grammars) ──", "struct TextSample { const char* name; const char* text; };"]
    )
    for sensor, label in (("irr_program_id", "ProgramId"), ("irr_state", "State"), ("irr_events", "Events")):
        samples = text[sensor]
        out.append(f"static const size_t k{label}SampleCount = {len(samples)};")
        out.append(f"static const TextSample k{label}Samples[] = {{")
        for sample in samples:
            out.append(f"    {{{_c_string(sample['name'])}, {_c_string(sample['text'])}}},")
        out.append("};")
    code_of = {outcome: code for code, outcome in irr.OUTCOME_CODES.items()}
    out.extend(
        [
            "struct GoldenEvent {",
            "  uint16_t seq; uint8_t line; uint16_t slot_id; uint16_t local_day; const char* outcome; const char* reason;",
            "};",
            "// Each irr_events sample's parsed entries, oldest first.",
            f"static const GoldenEvent kEventsParsed[][{irr.IRR_EVENTS_MAX}] = {{",
        ]
    )
    counts = []
    for sample in text["irr_events"]:
        entries = []
        for event in sample["parsed"]:
            day = (date.fromisoformat(event["local_day"]) - irr.EVENT_DAY_EPOCH).days
            entries.append(
                f"{{{event['seq']}, {irr.DEVICE_IDX[event['line_id']]}, {event['slot_id'] or 0}, {day}, "
                f"{_c_string(code_of[irr.RunOutcome(event['outcome'])])}, {_c_string(event['reason'] or '')}}}"
            )
        counts.append(str(len(entries)))
        out.append(
            f"    {{{', '.join(entries)}}},  // {sample['name']}" if entries else f"    {{}},  // {sample['name']}"
        )
    out.append("};")
    out.append(f"static const uint8_t kEventsParsedCount[] = {{{', '.join(counts)}}};")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify the committed fixture matches regeneration")
    args = parser.parse_args()

    content = json.dumps(build_fixture(), indent=2) + "\n"
    include = render_include(json.loads(content))
    if args.check:
        drift = []
        for path, expected in ((FIXTURE_PATH, content), (INC_PATH, include)):
            current = path.read_text() if path.exists() else None
            if current != expected:
                drift.append(str(path.relative_to(REPO_ROOT)))
        if drift:
            print("DRIFT: regenerate with python3 scripts/gen-irrigation-program-vectors.py:", ", ".join(drift))
            return 1
        print("irrigation program vectors fixture and firmware include are up to date")
        return 0
    FIXTURE_PATH.write_text(content)
    INC_PATH.write_text(include)
    print(f"wrote {FIXTURE_PATH.relative_to(REPO_ROOT)}")
    print(f"wrote {INC_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
