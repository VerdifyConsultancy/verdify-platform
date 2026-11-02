"""Irrigation program record VIP1 and the controller's irrigation text sensors (VD-36, #1151).

This module is the frozen contract between the platform and the ESP32 irrigation engine
(irrigation §5, ADR-0011). The compiler (#1152) packs schedules into the record here, the
ingestor (#1157) pushes it in chunks and stores the outcomes it parses here, the API and MCP
(#1155) read state through the parsers here, and the firmware decoder (#1159) copies the codes
below into C++ and runs its native tests on the golden vectors in
``verdify_schemas/tests/fixtures/irrigation_program_vectors.json``
(``scripts/gen-irrigation-program-vectors.py``). It is pure: stdlib and pydantic only, nothing
from ``ingestor/``, so the api, MCP and ingestor images all load it.

Program record, 584 B, little-endian, SHA-256 over all of it::

    0-15     header  '<4sHBBHBBB3x'   magic b"VIP1" (also the format version) | saf4_gal u16 |
                                      rh_ceiling_pct u8 | climate_yield_min u8 | enabled_mask u16
                                      (bit = device_idx) | vpd_member_mask u8 | schedule_member_mask u8
                                      (MISTER_MASK_BIT) | slot_count u8 | 3 reserved zero bytes
    16-103   lines   '<HHI' x 11      daily_cap_min | vpd_reserve_min | paused_until (UTC epoch s,
                                      0 = none), by device_idx
    104-583  slots   '<HBBBBhHHHHB3x' x 24
                                      slot_id | line (device_idx) | weekday_mask (bit 0 = Monday) |
                                      start_kind | flags (bit 0 = degraded_only) | start i16 | on_s |
                                      gap_s | prewet_s | flush_s | count | 3 reserved zero bytes

Used slots come first, in ascending ``slot_id``; unused slots and every reserved byte are zero.
The record has no revision, no per-occurrence skips (Q-03) and no flow fields (Q-10), so an
unchanged program always hashes the same. The models check the record ranges; the tighter bounds
per line kind (irrigation §4.3) are #1152's ``BOUNDS_BY_KIND``, which the firmware decoder also
applies, so every golden vector stays inside them.

Text sensors. Each value is at most 255 characters (the Home Assistant state limit); numbers are
plain decimal with no sign or leading zero; ``<idx>`` is a ``device_idx`` (L01 = 0 to L11 = 10);
epochs are UTC seconds (u32)::

    irr_program_id  h=<16 lowercase hex> src=nvs|rom      src=rom always reads h=0000000000000000
    irr_state       space-separated tokens, each optional, in any order; the formatter writes
                    d, m, w..., f..., v, j:
                    d=<idx>,<cause>,<slot>,<start_epoch>,<end_epoch>   the run on the drip manifold
                    m=<idx>,<cause>,<slot>,<start_epoch>,<end_epoch>   the run on the mist manifold
                    w=<idx>,<reason>[,<until_epoch>]   a due run waiting or blocked (repeatable,
                                                       earliest due first)
                    f=<idx>,b|l,<until_epoch>          mister back-off (b) or latch (l), one per line
                    v=<c><s><w>                        VPD source for center, south, west:
                                                       o own, p peer, h house
                    j=<reject>                         last push reject (REJECT_CODES)
    irr_events      up to 8 space-separated entries, oldest first:
                    <seq>,<idx>,<slot>,<local_day>,<outcome>[,<reason>]
                    seq u16 (serial arithmetic: 65535 then 0) | slot 0 = run-now |
                    local_day u16 = America/Denver days since 1970-01-01 | outcome OUTCOME_CODES

In a run token ``<idx>`` is the line whose relay is open (a fertilizer run names its clean partner
during prewet and flush and the fertilizer line during the feed) and ``<end_epoch>`` is the planned
end; slot 0 means no slot (run-now, or a VPD pulse) in run tokens too, and parses to ``None``.
Past 255 characters the ``irr_state`` formatter drops ``w=`` tokens, latest due first; eight full
events fit in 223. Every parser raises ``ValueError`` on text outside the grammar, an unknown
token, cause, outcome or reason code included, and callers treat that as no reading. Nothing
parses to ``unknown``: ``IrrigationCause.UNKNOWN`` and ``RunOutcome.UNKNOWN`` are the platform's.
"""

from __future__ import annotations

import hashlib
import re
import struct
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import Annotated, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator, model_validator

__all__ = [
    "DEGRADED_ONLY_IDX",
    "DEVICE_IDX",
    "DRIP_MANIFOLD_IDX",
    "EVENT_DAY_EPOCH",
    "FAULT_CODES",
    "IRR_EVENTS_MAX",
    "IRR_TEXT_MAX_LEN",
    "LINE_IDS",
    "MISTER_MASK_BIT",
    "MIST_MANIFOLD_IDX",
    "OUTCOME_CODES",
    "PROGRAM_CHUNK_MAX",
    "PROGRAM_HEADER_FORMAT",
    "PROGRAM_LINE_COUNT",
    "PROGRAM_LINE_FORMAT",
    "PROGRAM_LINES_OFFSET",
    "PROGRAM_MAGIC",
    "PROGRAM_MAX_SLOTS",
    "PROGRAM_RECORD_LEN",
    "PROGRAM_SLOT_FORMAT",
    "PROGRAM_SLOTS_OFFSET",
    "REASON_WORDS",
    "REJECT_CODES",
    "ROM_HASH16",
    "SCHEDULABLE_IDX",
    "SLOT_FLAG_DEGRADED_ONLY",
    "START_KIND",
    "START_RANGE",
    "VPD_SOURCE_CODES",
    "FaultKind",
    "IrrEvent",
    "IrrFault",
    "IrrRun",
    "IrrState",
    "IrrWaiting",
    "IrrigationCause",
    "LineId",
    "ProgramId",
    "ProgramRecord",
    "ProgramRecordError",
    "ReasonCode",
    "RecordLine",
    "RecordSlot",
    "RejectCode",
    "RunOutcome",
    "VpdSource",
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
]

# ── Record layout ────────────────────────────────────────────────────────────

PROGRAM_MAGIC = b"VIP1"
PROGRAM_HEADER_FORMAT = "<4sHBBHBBB3x"
PROGRAM_LINE_FORMAT = "<HHI"
PROGRAM_SLOT_FORMAT = "<HBBBBhHHHHB3x"
PROGRAM_LINE_COUNT = 11
PROGRAM_MAX_SLOTS = 24
PROGRAM_LINES_OFFSET = struct.calcsize(PROGRAM_HEADER_FORMAT)  # 16
_LINE_LEN = struct.calcsize(PROGRAM_LINE_FORMAT)  # 8
_SLOT_LEN = struct.calcsize(PROGRAM_SLOT_FORMAT)  # 20
PROGRAM_SLOTS_OFFSET = PROGRAM_LINES_OFFSET + PROGRAM_LINE_COUNT * _LINE_LEN  # 104
PROGRAM_RECORD_LEN = PROGRAM_SLOTS_OFFSET + PROGRAM_MAX_SLOTS * _SLOT_LEN  # 584
PROGRAM_CHUNK_MAX = 192  # irr_program_chunk(offset, data_hex) carries at most 192 B
SLOT_FLAG_DEGRADED_ONLY = 0x01

# ── Fixed codes (#1159 copies these into C++) ────────────────────────────────

LineId = Literal["L01", "L02", "L03", "L04", "L05", "L06", "L07", "L08", "L09", "L10", "L11"]
LINE_IDS: tuple[str, ...] = get_args(LineId)  # position = device_idx
DEVICE_IDX: Mapping[str, int] = MappingProxyType({line_id: idx for idx, line_id in enumerate(LINE_IDS)})
# Bits of vpd_member_mask and schedule_member_mask.
MISTER_MASK_BIT: Mapping[str, int] = MappingProxyType({"L04": 0, "L07": 1, "L09": 2})
START_KIND: Mapping[str, int] = MappingProxyType({"clock": 0, "sunrise": 1, "solar_noon": 2, "sunset": 3})
# Inclusive `start` range per start kind: a clock minute of the day, or the offset from the solar time.
START_RANGE: Mapping[int, tuple[int, int]] = MappingProxyType(
    {0: (0, 1439), 1: (-240, 240), 2: (-240, 240), 3: (-240, 240)}
)
# Lines that take schedules: L01, L02 and L04-L10 (L03 opens with fertilizer runs; L11 fog is climate only).
SCHEDULABLE_IDX: frozenset[int] = frozenset({0, 1, 3, 4, 5, 6, 7, 8, 9})
# The two manifolds the schedulable lines share: one drip relay and one mister relay at a time.
DRIP_MANIFOLD_IDX: frozenset[int] = frozenset({0, 1, 4, 5})  # L01, L02, L05, L06
MIST_MANIFOLD_IDX: frozenset[int] = frozenset({3, 6, 7, 8, 9})  # L04, L07, L08, L09, L10
# degraded_only is allowed on the clean misters only: L04, L07 and L09.
DEGRADED_ONLY_IDX: frozenset[int] = frozenset(DEVICE_IDX[line_id] for line_id in MISTER_MASK_BIT)


class IrrigationCause(StrEnum):
    """Why water ran (the OpenAPI ``IrrigationCause``, same order). Only the platform assigns ``unknown``."""

    VPD = "vpd"
    SCHEDULE = "schedule"
    RUN_NOW = "run_now"
    FEED = "feed"
    PREWET = "prewet"
    FLUSH = "flush"
    FALLBACK = "fallback"
    UNKNOWN = "unknown"


class RunOutcome(StrEnum):
    """How an occurrence or run-now ended (the OpenAPI ``RunOutcome``, same order).

    The controller reports the first seven; only the platform assigns ``unknown``, to an outcome
    that never reached it (aged out of ``irr_events``).
    """

    OK = "ok"
    SHORT = "short"
    STOPPED = "stopped"
    PREEMPTED = "preempted"
    SKIPPED = "skipped"
    MISSED = "missed"
    CAP = "cap"
    UNKNOWN = "unknown"


# irr_events outcome codes. There is deliberately no code for RunOutcome.UNKNOWN.
OUTCOME_CODES: Mapping[str, RunOutcome] = MappingProxyType(
    {
        "ok": RunOutcome.OK,
        "sh": RunOutcome.SHORT,
        "st": RunOutcome.STOPPED,
        "pe": RunOutcome.PREEMPTED,
        "sk": RunOutcome.SKIPPED,
        "mi": RunOutcome.MISSED,
        "ca": RunOutcome.CAP,
    }
)
_OUTCOME_CODE_OF: Mapping[RunOutcome, str] = MappingProxyType(
    {outcome: code for code, outcome in OUTCOME_CODES.items()}
)
_DEVICE_CAUSES = frozenset(cause.value for cause in IrrigationCause if cause is not IrrigationCause.UNKNOWN)

# Reason codes shared by w= tokens and irr_events, with the words the API (#1155) and the Iris
# context print for them.
ReasonCode = Literal[
    "dis",
    "pau",
    "nsm",
    "nvm",
    "vok",
    "bof",
    "flt",
    "occ",
    "cap",
    "res",
    "rh",
    "dew",
    "clk",
    "man",
    "sf",
    "bud",
    "saf",
    "l03",
    "rst",
    "cmd",
]
REASON_WORDS: Mapping[str, str] = MappingProxyType(
    {
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
)

# Push rejects: the j= token, and the code unpack_program() gives each rejected record.
RejectCode = Literal["bad_length", "bad_magic", "bad_hash", "bad_header", "bad_line", "bad_slot"]
REJECT_CODES: tuple[str, ...] = get_args(RejectCode)

VpdSource = Literal["own", "peer", "house"]
VPD_SOURCE_CODES: Mapping[str, str] = MappingProxyType({"o": "own", "p": "peer", "h": "house"})
_VPD_SOURCE_CODE_OF: Mapping[str, str] = MappingProxyType({word: code for code, word in VPD_SOURCE_CODES.items()})

# f= kinds, named as the OpenAPI LineState fault: a back-off, or a latch until midnight or clear_fault.
FaultKind = Literal["backing_off", "mister_fault"]
FAULT_CODES: Mapping[str, str] = MappingProxyType({"b": "backing_off", "l": "mister_fault"})
_FAULT_CODE_OF: Mapping[str, str] = MappingProxyType({word: code for code, word in FAULT_CODES.items()})

ROM_HASH16 = "0" * 16
IRR_TEXT_MAX_LEN = 255
IRR_EVENTS_MAX = 8
EVENT_DAY_EPOCH = date(1970, 1, 1)

_U16 = Annotated[int, Field(strict=True, ge=0, le=0xFFFF)]
_U32 = Annotated[int, Field(strict=True, ge=0, le=0xFFFF_FFFF)]
_SlotId = Annotated[int, Field(strict=True, ge=1, le=0xFFFF)]
_FROZEN = ConfigDict(frozen=True, extra="forbid")


# ── Record models ────────────────────────────────────────────────────────────


class RecordLine(BaseModel):
    """One line's 8 B entry, by ``device_idx``: its daily cap and VPD reserve in minutes and its pause."""

    model_config = _FROZEN

    daily_cap_min: Annotated[int, Field(strict=True, ge=0, le=480)] = 0  # 0 = no cap
    vpd_reserve_min: Annotated[int, Field(strict=True, ge=0, le=480)] = 0
    paused_until: _U32 = 0  # UTC epoch seconds, 0 = not paused

    @model_validator(mode="after")
    def _reserve_within_cap(self) -> RecordLine:
        if self.vpd_reserve_min > self.daily_cap_min:
            raise ValueError(f"vpd_reserve_min {self.vpd_reserve_min} exceeds daily_cap_min {self.daily_cap_min}")
        return self


class RecordSlot(BaseModel):
    """One used 20 B schedule slot. ``line`` is the ``device_idx``; ``degraded_only`` is flag bit 0."""

    model_config = _FROZEN

    slot_id: _SlotId
    line: Annotated[int, Field(strict=True, ge=0, le=10)]
    weekday_mask: Annotated[int, Field(strict=True, ge=1, le=0x7F)]  # bit 0 = Monday
    start_kind: Annotated[int, Field(strict=True, ge=0, le=3)]  # START_KIND
    degraded_only: StrictBool = False
    start: Annotated[int, Field(strict=True, ge=-240, le=1439)]  # START_RANGE by kind
    on_s: Annotated[int, Field(strict=True, ge=10, le=7200)]
    gap_s: Annotated[int, Field(strict=True, ge=0, le=600)] = 0
    prewet_s: Annotated[int, Field(strict=True, ge=0, le=1800)] = 0
    flush_s: Annotated[int, Field(strict=True, ge=0, le=1800)] = 0
    count: Annotated[int, Field(strict=True, ge=1, le=60)] = 1

    @model_validator(mode="after")
    def _record_rules(self) -> RecordSlot:
        if self.line not in SCHEDULABLE_IDX:
            raise ValueError(f"line {LINE_IDS[self.line]} takes no schedules (only L01, L02 and L04-L10)")
        lo, hi = START_RANGE[self.start_kind]
        if not lo <= self.start <= hi:
            raise ValueError(f"start {self.start} is outside {lo}..{hi} for start_kind {self.start_kind}")
        if self.degraded_only and self.line not in DEGRADED_ONLY_IDX:
            raise ValueError(f"degraded_only is allowed on L04, L07 and L09 only, not {LINE_IDS[self.line]}")
        return self


class ProgramRecord(BaseModel):
    """The compiled irrigation program the controller keeps in NVS and runs offline (``VIP1``).

    ``lines`` holds all 11 lines by ``device_idx``; ``slots`` holds the used slots in ascending
    ``slot_id``. ``slot_count`` is ``len(slots)``. Every number is a strict ``int`` (a float, bool
    or string is refused, never rounded), so the compiler converts and snaps database values first.
    """

    model_config = _FROZEN

    saf4_gal: Annotated[int, Field(strict=True, ge=100, le=600)]
    rh_ceiling_pct: Annotated[int, Field(strict=True, ge=75, le=98)]
    climate_yield_min: Annotated[int, Field(strict=True, ge=5, le=30)]
    enabled_mask: Annotated[int, Field(strict=True, ge=0, le=0x7FF)] = 0  # bit = device_idx
    vpd_member_mask: Annotated[int, Field(strict=True, ge=0, le=0x07)] = 0  # MISTER_MASK_BIT
    schedule_member_mask: Annotated[int, Field(strict=True, ge=0, le=0x07)] = 0  # MISTER_MASK_BIT
    lines: Annotated[tuple[RecordLine, ...], Field(min_length=PROGRAM_LINE_COUNT, max_length=PROGRAM_LINE_COUNT)]
    slots: Annotated[tuple[RecordSlot, ...], Field(max_length=PROGRAM_MAX_SLOTS)] = ()

    @field_validator("slots")
    @classmethod
    def _ascending_slot_ids(cls, slots: tuple[RecordSlot, ...]) -> tuple[RecordSlot, ...]:
        for prev, cur in pairwise(slots):
            if cur.slot_id <= prev.slot_id:
                raise ValueError(f"slots must be in ascending slot_id order: {prev.slot_id} then {cur.slot_id}")
        return slots

    @property
    def slot_count(self) -> int:
        return len(self.slots)


class ProgramRecordError(ValueError):
    """Why ``unpack_program()`` rejected a record; ``code`` is the reject the firmware decoder reports."""

    def __init__(self, code: RejectCode, detail: str) -> None:
        super().__init__(code, detail)
        self.code = code
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


# ── Pack, unpack, hash and chunk ─────────────────────────────────────────────


def pack_program(rec: ProgramRecord) -> bytes:
    """The record's exact 584 B. Deterministic: the same program always packs to the same bytes."""
    if not isinstance(rec, ProgramRecord):
        raise TypeError(f"pack_program() takes a ProgramRecord, not {type(rec).__name__}")
    out = bytearray(
        struct.pack(
            PROGRAM_HEADER_FORMAT,
            PROGRAM_MAGIC,
            rec.saf4_gal,
            rec.rh_ceiling_pct,
            rec.climate_yield_min,
            rec.enabled_mask,
            rec.vpd_member_mask,
            rec.schedule_member_mask,
            len(rec.slots),
        )
    )
    for line in rec.lines:
        out += struct.pack(PROGRAM_LINE_FORMAT, line.daily_cap_min, line.vpd_reserve_min, line.paused_until)
    for slot in rec.slots:
        out += struct.pack(
            PROGRAM_SLOT_FORMAT,
            slot.slot_id,
            slot.line,
            slot.weekday_mask,
            slot.start_kind,
            SLOT_FLAG_DEGRADED_ONLY if slot.degraded_only else 0,
            slot.start,
            slot.on_s,
            slot.gap_s,
            slot.prewet_s,
            slot.flush_s,
            slot.count,
        )
    out += bytes(_SLOT_LEN * (PROGRAM_MAX_SLOTS - len(rec.slots)))
    return bytes(out)


def _record_bytes(data: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(data, bytes | bytearray | memoryview):
        raise TypeError(f"expected the record's bytes, not {type(data).__name__}")
    raw = bytes(data)
    if len(raw) != PROGRAM_RECORD_LEN:
        raise ProgramRecordError("bad_length", f"{len(raw)} B, expected {PROGRAM_RECORD_LEN}")
    return raw


def _validated(model: type[BaseModel], code: RejectCode, where: str, **fields: object) -> BaseModel:
    try:
        return model(**fields)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(part) for part in first["loc"])
        raise ProgramRecordError(code, f"{where}: {loc + ': ' if loc else ''}{first['msg']}") from None


def unpack_program(data: bytes | bytearray | memoryview) -> ProgramRecord:
    """Decode and validate a record; raises ``ProgramRecordError`` (a ``ValueError``) on anything outside it.

    Checks run in the firmware decoder's order: length, magic, header, lines, then slots, and each
    failure carries that section's reject code. ``unpack_program(pack_program(r)) == r`` for every
    valid record.
    """
    raw = _record_bytes(data)
    magic, saf4, rh_ceiling, climate_yield, enabled, vpd_members, schedule_members, slot_count = struct.unpack_from(
        PROGRAM_HEADER_FORMAT, raw, 0
    )
    if magic != PROGRAM_MAGIC:
        raise ProgramRecordError("bad_magic", f"magic {magic!r}, expected {PROGRAM_MAGIC!r}")
    if any(raw[13:PROGRAM_LINES_OFFSET]):
        raise ProgramRecordError("bad_header", "reserved header bytes 13-15 are not zero")
    if slot_count > PROGRAM_MAX_SLOTS:
        raise ProgramRecordError("bad_header", f"slot_count {slot_count} exceeds {PROGRAM_MAX_SLOTS}")
    header = {
        "saf4_gal": saf4,
        "rh_ceiling_pct": rh_ceiling,
        "climate_yield_min": climate_yield,
        "enabled_mask": enabled,
        "vpd_member_mask": vpd_members,
        "schedule_member_mask": schedule_members,
    }
    _validated(ProgramRecord, "bad_header", "header", **header, lines=(RecordLine(),) * PROGRAM_LINE_COUNT)

    lines = []
    for idx, line_id in enumerate(LINE_IDS):
        cap, reserve, paused_until = struct.unpack_from(
            PROGRAM_LINE_FORMAT, raw, PROGRAM_LINES_OFFSET + idx * _LINE_LEN
        )
        lines.append(
            _validated(
                RecordLine,
                "bad_line",
                f"line {line_id}",
                daily_cap_min=cap,
                vpd_reserve_min=reserve,
                paused_until=paused_until,
            )
        )

    slots: list[RecordSlot] = []
    for index in range(PROGRAM_MAX_SLOTS):
        offset = PROGRAM_SLOTS_OFFSET + index * _SLOT_LEN
        entry = raw[offset : offset + _SLOT_LEN]
        if index >= slot_count:
            if any(entry):
                raise ProgramRecordError("bad_slot", f"unused slot {index} is not zero")
            continue
        if any(entry[17:]):
            raise ProgramRecordError("bad_slot", f"slot {index}: reserved bytes 17-19 are not zero")
        slot_id, line, weekday_mask, start_kind, flags, start, on_s, gap_s, prewet_s, flush_s, count = (
            struct.unpack_from(PROGRAM_SLOT_FORMAT, raw, offset)
        )
        if flags & ~SLOT_FLAG_DEGRADED_ONLY:
            raise ProgramRecordError("bad_slot", f"slot {index}: flag bits {flags:#04x} beyond bit 0 (degraded_only)")
        if slots and slot_id <= slots[-1].slot_id:
            raise ProgramRecordError(
                "bad_slot", f"slot {index}: slot_id {slot_id} is not above the previous {slots[-1].slot_id}"
            )
        slots.append(
            _validated(
                RecordSlot,
                "bad_slot",
                f"slot {index}",
                slot_id=slot_id,
                line=line,
                weekday_mask=weekday_mask,
                start_kind=start_kind,
                degraded_only=bool(flags & SLOT_FLAG_DEGRADED_ONLY),
                start=start,
                on_s=on_s,
                gap_s=gap_s,
                prewet_s=prewet_s,
                flush_s=flush_s,
                count=count,
            )
        )
    return _validated(ProgramRecord, "bad_header", "record", **header, lines=tuple(lines), slots=tuple(slots))


def program_sha256(data: bytes | bytearray | memoryview) -> str:
    """SHA-256 over all 584 B, as 64 lowercase hex: the hash ``irr_program_begin`` carries."""
    return hashlib.sha256(_record_bytes(data)).hexdigest()


def program_hash16(data: bytes | bytearray | memoryview) -> str:
    """The first 16 hex of ``program_sha256``: the value ``irr_program_id`` publishes as ``h=``."""
    return program_sha256(data)[:16]


def program_chunks(data: bytes | bytearray | memoryview, max_len: int = PROGRAM_CHUNK_MAX) -> list[tuple[int, str]]:
    """``(offset, lowercase hex)`` pairs for ``irr_program_chunk``: offsets 0, 192, 384 and 576 by default."""
    raw = _record_bytes(data)
    if not 1 <= max_len <= PROGRAM_CHUNK_MAX:
        raise ValueError(f"max_len {max_len} is outside 1..{PROGRAM_CHUNK_MAX}")
    return [(offset, raw[offset : offset + max_len].hex()) for offset in range(0, len(raw), max_len)]


# ── Text-sensor models ───────────────────────────────────────────────────────


def _require_idx(line_id: str, allowed: frozenset[int], what: str) -> None:
    if DEVICE_IDX[line_id] not in allowed:
        raise ValueError(f"{line_id} {what}")


class ProgramId(BaseModel):
    """``irr_program_id``: the hash of the program the controller runs, and whether it came from NVS or ROM."""

    model_config = _FROZEN

    hash16: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{16}$")]
    src: Literal["nvs", "rom"]

    @model_validator(mode="after")
    def _rom_reads_zero(self) -> ProgramId:
        if self.src == "rom" and self.hash16 != ROM_HASH16:
            raise ValueError(f"src=rom always reads h={ROM_HASH16}")
        return self


class IrrRun(BaseModel):
    """A run token: the run open on the drip manifold (``d=``) or the mist manifold (``m=``).

    ``slot_id`` is ``None`` for slot 0 (run-now, or a VPD pulse). ``end_epoch`` is the planned end,
    republished only on a pause or resume.
    """

    model_config = _FROZEN

    line_id: LineId
    cause: IrrigationCause
    slot_id: _SlotId | None = None
    start_epoch: _U32
    end_epoch: _U32

    @model_validator(mode="after")
    def _device_rules(self) -> IrrRun:
        _require_idx(self.line_id, SCHEDULABLE_IDX, "is on neither manifold")
        if self.cause == IrrigationCause.UNKNOWN:
            raise ValueError("cause unknown is the platform's, never the controller's")
        if self.end_epoch < self.start_epoch:
            raise ValueError(f"end_epoch {self.end_epoch} is before start_epoch {self.start_epoch}")
        return self

    @property
    def manifold(self) -> Literal["drip", "mist"]:
        # Every schedulable line sits on exactly one of the two manifolds.
        return "drip" if DEVICE_IDX[self.line_id] in DRIP_MANIFOLD_IDX else "mist"


class IrrWaiting(BaseModel):
    """A ``w=`` token: a due run waiting or blocked, with its reason code and, when known, until when."""

    model_config = _FROZEN

    line_id: LineId
    reason: ReasonCode
    until_epoch: _U32 | None = None

    @model_validator(mode="after")
    def _schedulable(self) -> IrrWaiting:
        _require_idx(self.line_id, SCHEDULABLE_IDX, "takes no runs")
        return self


class IrrFault(BaseModel):
    """An ``f=`` token: a mister line backing off or latched by the relay watchdog (D-IRP-19), and until when."""

    model_config = _FROZEN

    line_id: LineId
    fault: FaultKind
    until_epoch: _U32

    @model_validator(mode="after")
    def _mister(self) -> IrrFault:
        _require_idx(self.line_id, MIST_MANIFOLD_IDX, "is not a mister line")
        return self


class VpdSources(BaseModel):
    """The ``v=`` token: the VPD source each mister zone is using (D-IRP-13)."""

    model_config = _FROZEN

    center: VpdSource
    south: VpdSource
    west: VpdSource


class IrrState(BaseModel):
    """``irr_state``: the runs on the two manifolds, due runs waiting (earliest due first), faults,
    VPD sources and the last push reject. Every part is optional; ``runs`` is kept drip first."""

    model_config = _FROZEN

    runs: tuple[IrrRun, ...] = ()
    waiting: tuple[IrrWaiting, ...] = ()
    faults: tuple[IrrFault, ...] = ()
    vpd_source: VpdSources | None = None
    reject: RejectCode | None = None

    @field_validator("runs")
    @classmethod
    def _one_run_per_manifold(cls, runs: tuple[IrrRun, ...]) -> tuple[IrrRun, ...]:
        manifolds = [run.manifold for run in runs]
        if len(set(manifolds)) != len(manifolds):
            raise ValueError("at most one run per manifold")
        return tuple(sorted(runs, key=lambda run: run.manifold != "drip"))

    @field_validator("faults")
    @classmethod
    def _one_fault_per_line(cls, faults: tuple[IrrFault, ...]) -> tuple[IrrFault, ...]:
        line_ids = [fault.line_id for fault in faults]
        if len(set(line_ids)) != len(line_ids):
            raise ValueError("at most one f= token per line")
        return faults


class IrrEvent(BaseModel):
    """One ``irr_events`` entry: the outcome of an occurrence, or of a run-now (``slot_id`` ``None``)."""

    model_config = _FROZEN

    seq: _U16
    line_id: LineId
    slot_id: _SlotId | None = None
    local_day: date  # America/Denver
    outcome: RunOutcome
    reason: ReasonCode | None = None

    @model_validator(mode="after")
    def _device_rules(self) -> IrrEvent:
        _require_idx(self.line_id, SCHEDULABLE_IDX, "takes no runs")
        if self.outcome == RunOutcome.UNKNOWN:
            raise ValueError("outcome unknown is the platform's, never the controller's")
        if not 0 <= (self.local_day - EVENT_DAY_EPOCH).days <= 0xFFFF:
            raise ValueError(f"local_day {self.local_day} is outside 1970-01-01 plus 0..65535 days")
        return self


# ── Text-sensor parsers and formatters ───────────────────────────────────────

_DECIMAL = re.compile(r"0|[1-9][0-9]*")
_PROGRAM_ID = re.compile(r"h=([0-9a-f]{16}) src=(nvs|rom)")
_VPD_TOKEN = re.compile(r"[oph]{3}")
_STATE_KEYS = frozenset("dmwfvj")
_SINGLE_STATE_KEYS = frozenset("dmvj")


def _check_text(text: str, sensor: str) -> None:
    if not isinstance(text, str):
        raise TypeError(f"{sensor} must be str, not {type(text).__name__}")
    if len(text) > IRR_TEXT_MAX_LEN:
        raise ValueError(f"{sensor} is {len(text)} characters; the limit is {IRR_TEXT_MAX_LEN}")


def _words(text: str, sensor: str) -> list[str]:
    if text == "":
        return []
    words = text.split(" ")
    if "" in words:
        raise ValueError(f"{sensor} {text!r} has an empty token (a doubled, leading or trailing space)")
    return words


def _number(raw: str, field: str, hi: int) -> int:
    if not _DECIMAL.fullmatch(raw):
        raise ValueError(f"{field} {raw!r} is not a plain decimal number")
    value = int(raw)
    if value > hi:
        raise ValueError(f"{field} {value} exceeds {hi}")
    return value


def _line_id(raw: str) -> str:
    return LINE_IDS[_number(raw, "line index", PROGRAM_LINE_COUNT - 1)]


def _slot_id(raw: str) -> int | None:
    return _number(raw, "slot", 0xFFFF) or None


def _reason(raw: str) -> str:
    if raw not in REASON_WORDS:
        raise ValueError(f"unknown reason code {raw!r}")
    return raw


def parse_irr_program_id(text: str) -> ProgramId:
    """Parse ``h=<16 hex> src=nvs|rom``."""
    _check_text(text, "irr_program_id")
    match = _PROGRAM_ID.fullmatch(text)
    if match is None:
        raise ValueError(f"irr_program_id {text!r} is not 'h=<16 lowercase hex> src=nvs|rom'")
    return ProgramId(hash16=match[1], src=match[2])


def format_irr_program_id(program_id: ProgramId) -> str:
    """The ``irr_program_id`` text for a ``ProgramId``."""
    return f"h={program_id.hash16} src={program_id.src}"


def _parse_run(key: str, value: str) -> IrrRun:
    fields = value.split(",")
    if len(fields) != 5:
        raise ValueError(f"{key}= takes <idx>,<cause>,<slot>,<start_epoch>,<end_epoch>, not {value!r}")
    idx, cause, slot, start, end = fields
    if cause not in _DEVICE_CAUSES:
        raise ValueError(f"unknown cause {cause!r}")
    run = IrrRun(
        line_id=_line_id(idx),
        cause=IrrigationCause(cause),
        slot_id=_slot_id(slot),
        start_epoch=_number(start, "start_epoch", 0xFFFF_FFFF),
        end_epoch=_number(end, "end_epoch", 0xFFFF_FFFF),
    )
    if run.manifold[0] != key:
        raise ValueError(f"{run.line_id} runs on the {run.manifold} manifold, not in a {key}= token")
    return run


def _parse_waiting(value: str) -> IrrWaiting:
    fields = value.split(",")
    if len(fields) not in (2, 3):
        raise ValueError(f"w= takes <idx>,<reason>[,<until_epoch>], not {value!r}")
    until = _number(fields[2], "until_epoch", 0xFFFF_FFFF) if len(fields) == 3 else None
    return IrrWaiting(line_id=_line_id(fields[0]), reason=_reason(fields[1]), until_epoch=until)


def _parse_fault(value: str) -> IrrFault:
    fields = value.split(",")
    if len(fields) != 3 or fields[1] not in FAULT_CODES:
        raise ValueError(f"f= takes <idx>,b|l,<until_epoch>, not {value!r}")
    return IrrFault(
        line_id=_line_id(fields[0]),
        fault=FAULT_CODES[fields[1]],
        until_epoch=_number(fields[2], "until_epoch", 0xFFFF_FFFF),
    )


def _parse_vpd_sources(value: str) -> VpdSources:
    if not _VPD_TOKEN.fullmatch(value):
        raise ValueError(f"v= takes three of o, p and h (center, south, west), not {value!r}")
    center, south, west = (VPD_SOURCE_CODES[code] for code in value)
    return VpdSources(center=center, south=south, west=west)


def parse_irr_state(text: str) -> IrrState:
    """Parse ``irr_state``. Tokens may come in any order; ``w=`` and ``f=`` tokens keep theirs."""
    _check_text(text, "irr_state")
    runs: list[IrrRun] = []
    waiting: list[IrrWaiting] = []
    faults: list[IrrFault] = []
    vpd_source: VpdSources | None = None
    reject: str | None = None
    seen: set[str] = set()
    for token in _words(text, "irr_state"):
        key, sep, value = token.partition("=")
        if not sep or key not in _STATE_KEYS:
            raise ValueError(f"unknown irr_state token {token!r}")
        if key in _SINGLE_STATE_KEYS:
            if key in seen:
                raise ValueError(f"irr_state has more than one {key}= token")
            seen.add(key)
        if key in ("d", "m"):
            runs.append(_parse_run(key, value))
        elif key == "w":
            waiting.append(_parse_waiting(value))
        elif key == "f":
            faults.append(_parse_fault(value))
        elif key == "v":
            vpd_source = _parse_vpd_sources(value)
        elif value in REJECT_CODES:
            reject = value
        else:
            raise ValueError(f"unknown push reject {value!r}")
    return IrrState(
        runs=tuple(runs), waiting=tuple(waiting), faults=tuple(faults), vpd_source=vpd_source, reject=reject
    )


def format_irr_state(state: IrrState) -> str:
    """The ``irr_state`` text: d, m, w..., f..., v, j. Past 255 characters, ``w=`` tokens drop, latest due first."""
    if not isinstance(state, IrrState):
        raise TypeError(f"format_irr_state() takes an IrrState, not {type(state).__name__}")
    head = [
        f"{run.manifold[0]}={DEVICE_IDX[run.line_id]},{run.cause.value},{run.slot_id or 0},"
        f"{run.start_epoch},{run.end_epoch}"
        for run in state.runs
    ]
    waits = [
        f"w={DEVICE_IDX[item.line_id]},{item.reason}" + ("" if item.until_epoch is None else f",{item.until_epoch}")
        for item in state.waiting
    ]
    tail = [
        f"f={DEVICE_IDX[fault.line_id]},{_FAULT_CODE_OF[fault.fault]},{fault.until_epoch}" for fault in state.faults
    ]
    if state.vpd_source is not None:
        sources = state.vpd_source
        tail.append("v=" + "".join(_VPD_SOURCE_CODE_OF[word] for word in (sources.center, sources.south, sources.west)))
    if state.reject is not None:
        tail.append(f"j={state.reject}")
    while True:
        text = " ".join([*head, *waits, *tail])
        if len(text) <= IRR_TEXT_MAX_LEN:
            return text
        if not waits:  # unreachable: runs, faults, v and j together stay under 190 characters
            raise ValueError(f"irr_state needs {len(text)} characters without any w= token")
        waits.pop()


def parse_irr_events(text: str) -> list[IrrEvent]:
    """Parse ``irr_events``: up to 8 entries, oldest first by u16 serial order."""
    _check_text(text, "irr_events")
    entries = _words(text, "irr_events")
    if len(entries) > IRR_EVENTS_MAX:
        raise ValueError(f"irr_events has {len(entries)} entries; at most {IRR_EVENTS_MAX}")
    events: list[IrrEvent] = []
    for entry in entries:
        fields = entry.split(",")
        if len(fields) not in (5, 6):
            raise ValueError(f"irr_events entry {entry!r} is not <seq>,<idx>,<slot>,<local_day>,<outcome>[,<reason>]")
        seq, idx, slot, local_day, code = fields[:5]
        if code not in OUTCOME_CODES:
            raise ValueError(f"unknown outcome code {code!r}")
        event = IrrEvent(
            seq=_number(seq, "seq", 0xFFFF),
            line_id=_line_id(idx),
            slot_id=_slot_id(slot),
            local_day=EVENT_DAY_EPOCH + timedelta(days=_number(local_day, "local_day", 0xFFFF)),
            outcome=OUTCOME_CODES[code],
            reason=_reason(fields[5]) if len(fields) == 6 else None,
        )
        if events and not seq_newer(event.seq, events[-1].seq):
            raise ValueError(f"irr_events is not oldest first: seq {event.seq} after {events[-1].seq}")
        events.append(event)
    return events


def format_irr_events(events: Sequence[IrrEvent]) -> str:
    """The ``irr_events`` text for up to 8 events, oldest first."""
    events = list(events)
    if len(events) > IRR_EVENTS_MAX:
        raise ValueError(f"{len(events)} events; irr_events holds at most {IRR_EVENTS_MAX}")
    for prev, cur in pairwise(events):
        if not seq_newer(cur.seq, prev.seq):
            raise ValueError(f"events are not oldest first: seq {cur.seq} after {prev.seq}")
    return " ".join(
        f"{event.seq},{DEVICE_IDX[event.line_id]},{event.slot_id or 0},{(event.local_day - EVENT_DAY_EPOCH).days},"
        f"{_OUTCOME_CODE_OF[event.outcome]}" + ("" if event.reason is None else f",{event.reason}")
        for event in events
    )


def seq_newer(a: int, b: int) -> bool:
    """True when sequence number ``a`` is newer than ``b`` in u16 serial arithmetic, so 0 follows 65535."""
    for value in (a, b):
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFF:
            raise ValueError(f"sequence number {value!r} is not a u16")
    return 0 < (a - b) & 0xFFFF < 0x8000
