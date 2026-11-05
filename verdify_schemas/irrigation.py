"""Irrigation program record VIP1, the controller's irrigation text sensors (VD-36, #1151), the
schedule model that compiles into the record (VD-37, #1152) and the run and stop commands (VD-64,
#1177).

This module is the frozen contract between the platform and the ESP32 irrigation engine
(irrigation §5, ADR-0011). The compiler below packs schedules into the record here, the
ingestor (#1157) pushes it in chunks and stores the outcomes it parses here, the API and MCP
(#1155) read state through the parsers here, and the firmware decoder (#1159) copies the codes
below into C++ and runs its native tests on the golden vectors in
``verdify_schemas/tests/fixtures/irrigation_program_vectors.json``
(``scripts/gen-irrigation-program-vectors.py``). It is pure: stdlib, pydantic and its sibling
modules ``solar``, ``setting_values`` and ``tunable_registry`` only, nothing from ``ingestor/``,
so the api, MCP and ingestor images all load it.

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
per line kind (irrigation §4.3) are ``BOUNDS_BY_KIND`` below, which the firmware decoder also
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
end; a ``degraded_only`` run carries cause ``fallback``; slot 0 means no slot (run-now, or a VPD
pulse) in run tokens too, and parses to ``None``. Past 255 characters the ``irr_state`` formatter
drops ``w=`` tokens, latest due first; eight full events fit in 223. Every parser raises
``ValueError`` on text outside the grammar, an unknown token, cause, outcome or reason code
included, and callers treat that as no reading. Nothing parses to ``unknown``:
``IrrigationCause.UNKNOWN`` and ``RunOutcome.UNKNOWN`` are the platform's.

The parsers also reject the following, so the controller's formatters (#1159, #1160) never write
it: anything but exactly one space between tokens or entries (none leading or trailing); a ``d=``
naming a line other than L01, L02, L05 or L06, or an ``m=`` naming one other than L04, L07-L10 or a fog run-once on
L11 (cause ``run_now`` only, #1343 follow-up);
more than one ``d=``, ``m=``, ``v=`` or ``j=``; an ``<end_epoch>`` before its ``<start_epoch>``; a
``w=`` token naming L03 or L11, or an ``irr_events`` entry naming L03; an ``f=`` naming a line other than the
misters L04 and L07-L10, or naming one line twice; and an ``irr_events`` entry that is not newer than
the one before it by ``seq_newer``.

Schedule model (VD-37; irrigation §4.1-§4.3). ``LineSpec`` and ``ScheduleSpec`` carry M3's
``irrigation_line`` and ``irrigation_slot`` rows (``from_row``), ``SiteValues`` the three
``irrigation_site`` settings (``site_values``), and ``RunArgs`` the run-now body per line kind.
Fractional seconds and minutes snap to whole numbers (``setting_values.snap``, half to even). The
submit checks return a ``Refusal`` in the OpenAPI ``Problem`` shape, or ``None``:
``validate_schedule`` refuses only ``unknown_target``, ``out_of_range`` (``BOUNDS_BY_KIND``),
``too_close`` (two starts on one line under ``MIN_SPACING_MIN`` apart in the next 366 days) and
``too_many_schedules`` (``MAX_SCHEDULES``); ``validate_line`` and ``validate_run`` check the line
caps and the run fields (``LINE_BOUNDS``, ``RUN_BOUNDS_BY_KIND``). A disabled line, a mist
schedule on a zone that is not a member, a pause or a cap overrun settles at run time.
``compile_program`` packs the rows into the record above; ``agenda`` resolves the occurrences of
1-7 days ahead (and 0-7 back) in America/Denver with ``verdify_schemas.solar``.

Ingestor rows (VD-42, #1157). ``IrrigationPushRow`` and ``IrrigationLedgerRow`` are M3's ``irrigation_push`` and
``irrigation_ledger`` rows, and ``RunClose`` a ledger row's closing columns; the ingestor validates its writes through
them. Only the ingestor writes these tables.

Line state (VD-40, #1155). ``line_state`` turns one ``v_irrigation_line_state`` row (M3, #1153) into a
line's state fields, pure, so the API and MCP (``verdify_services.irrigation.reads``) and Iris's context
(#1173, in the ingestor image) read water the same way.

A start is a minute on the local wall clock: a clock time, or that day's sunrise, solar noon or
sunset (``solar_times``: NOAA with the date's UTC offset at local noon, the math of the firmware's
``greenhouse_solar.h``) plus the offset. ``local_start`` places it at the first instant the
America/Denver clock reads it on that day: a time in the spring-forward gap runs when the clock
jumps past it, a repeated fall-back time runs the first time, and a solar start past midnight
(sunset + 240 min in June) falls early the next morning. Starts are returned at the fixed local
offset then in force, so they compare and serialize safely; spacing is measured between instants.

Commands (VD-64; irrigation §6.1 and §6.5). ``IrrigationCommandRow`` is M3's ``irrigation_command``
row: the services insert it ``pending`` and the ingestor validates each status update through it
(``sent`` with ``sent_at`` just before the device call, or ``failed`` with a ``fail_reason``).
``Command`` is the OpenAPI ``Command`` that run-now and stop answer with. ``COMMAND_SEND_WINDOW_S``
and ``STOP_RESEND_WINDOW_S`` are §6.1's two-minute limits: a command not sent within the first is
never sent, and a Stop's relays that have not read OFF are switched off again within the second.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta, timezone
from enum import StrEnum
from functools import lru_cache
from itertools import pairwise
from types import MappingProxyType
from typing import Annotated, Any, Literal, get_args
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    field_validator,
    model_validator,
)

from verdify_schemas.activity import ACTOR_NAME_MAX_LENGTH, ACTOR_SOURCE_PATTERN, REASON_MAX_LENGTH
from verdify_schemas.setting_values import snap, snap_setting
from verdify_schemas.solar import SolarTimes, compute_solar_times
from verdify_schemas.tunable_registry import IRRIGATION_SITE_PARAMS, REGISTRY

__all__ = [
    "BOUNDS_BY_KIND",
    "BRIDGE_RUN_LINE",
    "BRIDGE_RUN_WINDOW_S",
    "CAP_NOTE",
    "CIRCUITS",
    "CIRCUIT_BY_ID",
    "CIRCUIT_OF_LINE",
    "CLEAN_PARTNER",
    "CLIMATE_CAUSES",
    "COMMAND_KINDS",
    "COMMAND_SEND_WINDOW_S",
    "COMMAND_STATUSES",
    "DEGRADED_ONLY_IDX",
    "DEGRADED_ONLY_NOTE",
    "DEVICE_IDX",
    "DRIP_MANIFOLD_IDX",
    "EVENT_DAY_EPOCH",
    "FOG_LINE",
    "FAULT_CODES",
    "IRR_EVENTS_MAX",
    "IRR_TEXT_MAX_LEN",
    "LINE_BOUNDS",
    "LINE_IDS",
    "LINE_KIND",
    "LINE_KINDS",
    "LOCAL_TZ_NAME",
    "MAX_SCHEDULES",
    "MIN_SPACING_MIN",
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
    "RELAY_ONLY_LINES",
    "ROM_HASH16",
    "RUN_ARGS_BY_KIND",
    "RUN_BOUNDS_BY_KIND",
    "SCHEDULABLE_IDX",
    "SITE_PARAM_FIELDS",
    "SLOT_FLAG_DEGRADED_ONLY",
    "START_KIND",
    "START_RANGE",
    "STOP_RESEND_WINDOW_S",
    "TOPOLOGY_CAUSE",
    "VPD_SOURCE_CODES",
    "WAITING_REASONS",
    "WEEKDAYS",
    "Agenda",
    "AgendaOccurrence",
    "Command",
    "CommandActor",
    "CommandKind",
    "CommandStatus",
    "CompiledProgram",
    "DripRunArgs",
    "FaultKind",
    "FertDripRunArgs",
    "FertMisterRunArgs",
    "IrrEvent",
    "IrrFault",
    "IrrRun",
    "IrrState",
    "IrrWaiting",
    "IrrigationCause",
    "IrrigationCommandRow",
    "IrrigationLedgerRow",
    "IrrigationPushRow",
    "LineId",
    "LineKind",
    "LineSpec",
    "LineState",
    "LineStateCode",
    "LineStateFields",
    "MisterRunArgs",
    "ProgramId",
    "ProgramRecord",
    "ProgramRecordError",
    "ReasonCode",
    "RecordLine",
    "RecordSlot",
    "Refusal",
    "RefusalCode",
    "RejectCode",
    "RunArgs",
    "RunClose",
    "RunOutcome",
    "RunPurpose",
    "ScheduleSpec",
    "SiteValues",
    "SolarDay",
    "Start",
    "StartKind",
    "VpdSource",
    "VpdSources",
    "Weekday",
    "agenda",
    "bounds_by_kind",
    "compile_program",
    "days_from_mask",
    "days_to_api",
    "format_irr_events",
    "format_irr_program_id",
    "format_irr_state",
    "irr_state_reading",
    "line_state",
    "local_start",
    "local_tz",
    "occurrence_start",
    "pack_program",
    "parse_irr_events",
    "parse_irr_program_id",
    "parse_irr_state",
    "program_chunks",
    "program_hash16",
    "program_sha256",
    "purpose_of",
    "relay_cause",
    "run_args",
    "seq_newer",
    "site_values",
    "solar_times",
    "unpack_program",
    "utc_offset_min",
    "validate_line",
    "validate_run",
    "validate_schedule",
    "weekday_mask",
    "CircuitSpec",
    "CircuitId",
    "FogRunArgs",
    "MASTER_LINE",
    "RUN_IDX",
    "FOG_IDX",
    "VARIANTS",
    "Variant",
    "circuit_line",
    "master_valve_opens",
    "variant_of",
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
# Lines that take a run (#1343 follow-up): every schedulable line, and fog (L11), which may be run once. A fog run-once
# rides the m= token and names L11 in irr_events; fog still takes no schedules and no w= token names it.
FOG_IDX = 10
RUN_IDX: frozenset[int] = SCHEDULABLE_IDX | {FOG_IDX}


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


class RunPurpose(StrEnum):
    """What a run was for (#1348): ``irrigation`` waters plants (schedule, run-now, feed, prewet, flush and unknown on
    drips or misters); ``climate`` is VPD misting and fog (VPD pulses, sensor-fault fallback windows, and every fog
    run). The site's daily water limit (SAF-4) and the zone duty caps count both."""

    IRRIGATION = "irrigation"
    CLIMATE = "climate"


# The fog line is climate only: every L11 run is humidification, whatever cause it carries.
FOG_LINE = "L11"
CLIMATE_CAUSES: frozenset[str] = frozenset({"vpd", "fallback"})


def purpose_of(line_id: str, cause: str) -> RunPurpose:
    """A run's purpose from its line and cause (pure): fog, or a ``vpd`` or ``fallback`` cause, is climate."""
    if line_id == FOG_LINE or str(cause) in CLIMATE_CAUSES:
        return RunPurpose.CLIMATE
    return RunPurpose.IRRIGATION


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
        _require_idx(self.line_id, RUN_IDX, "is on neither manifold")
        if DEVICE_IDX[self.line_id] == FOG_IDX and self.cause != IrrigationCause.RUN_NOW:
            raise ValueError("a run on L11 is a fog run-once: its cause is run_now")
        if self.cause == IrrigationCause.UNKNOWN:
            raise ValueError("cause unknown is the platform's, never the controller's")
        if self.end_epoch < self.start_epoch:
            raise ValueError(f"end_epoch {self.end_epoch} is before start_epoch {self.start_epoch}")
        return self

    @property
    def manifold(self) -> Literal["drip", "mist"]:
        # Every schedulable line sits on exactly one of the two manifolds; a fog run-once rides the mist token.
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
        _require_idx(self.line_id, RUN_IDX, "takes no runs")
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


# ── Schedule model: lines, schedules, site values and runs (VD-37, #1152) ────

LineKind = Literal["drip", "mister", "fert_drip", "fert_mister", "master", "fog"]
LINE_KINDS: tuple[str, ...] = get_args(LineKind)
# Each line's kind and clean partner (irrigation §3; M3, #1153, seeds the same).
LINE_KIND: Mapping[str, str] = MappingProxyType(
    {
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
)
CLEAN_PARTNER: Mapping[str, str] = MappingProxyType({"L02": "L01", "L06": "L05", "L08": "L07", "L10": "L09"})

# ── Physical lines and their variants (#1336) ────────────────────────────────
# Jason (2026-11-05): five physical lines, each with a clean and a fertilized valve: Wall drip (all of the east, south
# and west walls and every shelf), Center drip, and the Center, South and West misters. The configured relays are the
# source of truth: a valve with no relay is "not wired" (``None``): shown, never scheduled or run. The fertilizer master
# valve (L03) connects the fertilizer tank and forces the flow through the feeder: it opens only with a fertilized
# variant, never with clean water (the firmware's irr_master_rule). Fog (L11) is a climate device, not a physical line.
# The relay ids stay the device and engine contract; a physical line maps a variant to its relay.
Variant = Literal["clean", "fertilized"]
VARIANTS: tuple[str, ...] = get_args(Variant)
MASTER_LINE = "L03"
CircuitId = Literal["wall_drip", "center_drip", "center_mister", "south_mister", "west_mister"]


class CircuitSpec(BaseModel):
    """One physical line: its name, zone and kind, and the relay of each variant (``None``: that valve is not wired)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    circuit_id: CircuitId
    name: str
    zone: Literal["wall", "center", "south", "west"]
    kind: Literal["drip", "mister"]
    clean_line: LineId | None
    fert_line: LineId | None

    def line_of(self, variant: str) -> str | None:
        """The relay of ``variant`` on this line; ``None`` when that valve is not wired."""
        return self.clean_line if variant == "clean" else self.fert_line if variant == "fertilized" else None


CIRCUITS: tuple[CircuitSpec, ...] = (
    CircuitSpec(circuit_id="wall_drip", name="Wall drip", zone="wall", kind="drip", clean_line="L01", fert_line="L02"),
    CircuitSpec(
        circuit_id="center_drip", name="Center drip", zone="center", kind="drip", clean_line="L05", fert_line="L06"
    ),
    CircuitSpec(
        circuit_id="center_mister", name="Center mister", zone="center", kind="mister", clean_line="L04", fert_line=None
    ),
    CircuitSpec(
        circuit_id="south_mister", name="South mister", zone="south", kind="mister", clean_line="L07", fert_line="L08"
    ),
    CircuitSpec(
        circuit_id="west_mister", name="West mister", zone="west", kind="mister", clean_line="L09", fert_line="L10"
    ),
)
CIRCUIT_BY_ID: Mapping[str, CircuitSpec] = MappingProxyType({c.circuit_id: c for c in CIRCUITS})
# The physical line and variant of each wired relay (L03 and L11 have none).
CIRCUIT_OF_LINE: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        line_id: (c.circuit_id, variant)
        for c in CIRCUITS
        for variant in VARIANTS
        if (line_id := c.line_of(variant)) is not None
    }
)


def variant_of(line_id: str) -> str | None:
    """``clean`` or ``fertilized`` for a relay on a physical line; ``None`` for L03, L11 and anything else."""
    found = CIRCUIT_OF_LINE.get(line_id)
    return None if found is None else found[1]


def master_valve_opens(line_id: str) -> bool:
    """Whether L03 opens with a run on ``line_id``: only a fertilized variant opens it, never clean water (#1336)."""
    return variant_of(line_id) == "fertilized"


def circuit_line(circuit_id: str, variant: str) -> Refusal | str:
    """The relay a physical line's variant runs on, or the refusal: ``unknown_target`` for a line or variant that is
    not one (``field`` circuit_id or variant) and for a valve that is not wired to a relay (``field`` variant)."""
    circuit = CIRCUIT_BY_ID.get(circuit_id)
    if circuit is None:
        names = ", ".join(CIRCUIT_BY_ID)
        return Refusal(
            code="unknown_target", field="circuit_id", detail=f"{circuit_id!r} is not a line; lines are {names}"
        )
    if variant not in VARIANTS:
        return Refusal(
            code="unknown_target", field="variant", detail=f"{variant!r} is not a variant; clean or fertilized"
        )
    line_id = circuit.line_of(variant)
    if line_id is None:
        return Refusal(
            code="unknown_target",
            field="variant",
            detail=f"{circuit.name}'s {variant} valve is not wired to a relay, so it cannot be scheduled or run",
        )
    return line_id


StartKind = Literal["clock", "sunrise", "solar_noon", "sunset"]
# The OpenAPI ScheduleDays names; position = weekday_mask bit = date.weekday() (Monday 0).
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[str, ...] = get_args(Weekday)
LOCAL_TZ_NAME = "America/Denver"

MAX_SCHEDULES = PROGRAM_MAX_SLOTS  # 24 stored schedules at most, enabled or not
MIN_SPACING_MIN = 30  # D-IRP-06: two starts on one line at least 30 min apart

_Range = tuple[int, int]
# Inclusive schedule field bounds per line kind (irrigation §4.3). The firmware decoder (#1159)
# applies the same table, so a slot outside it would be rejected with the whole record.
BOUNDS_BY_KIND: Mapping[str, Mapping[str, _Range]] = MappingProxyType(
    {
        "drip": MappingProxyType(
            {"on_s": (10, 7200), "count": (1, 1), "gap_s": (0, 0), "prewet_s": (0, 0), "flush_s": (0, 0)}
        ),
        "fert_drip": MappingProxyType(
            {"on_s": (10, 7200), "count": (1, 1), "gap_s": (0, 0), "prewet_s": (0, 1800), "flush_s": (0, 1800)}
        ),
        "mister": MappingProxyType(
            {"on_s": (10, 90), "count": (1, 60), "gap_s": (30, 600), "prewet_s": (0, 0), "flush_s": (0, 0)}
        ),
        "fert_mister": MappingProxyType(
            {"on_s": (10, 90), "count": (1, 60), "gap_s": (30, 600), "prewet_s": (0, 1800), "flush_s": (0, 1800)}
        ),
    }
)
# Run-now (the OpenAPI RunRequest) per line kind: drips run minutes; misters, fertilizer misters
# included, pulse with their kind's on_s, count and gap_s; both fertilizer kinds take a prewet and a
# flush on the clean partner; fog runs minutes of continuous fog. L03 takes no run of its own.
_RINSE: Mapping[str, _Range] = MappingProxyType({"prewet_s": (0, 1800), "flush_s": (0, 1800)})
RUN_BOUNDS_BY_KIND: Mapping[str, Mapping[str, _Range]] = MappingProxyType(
    {
        "drip": MappingProxyType({"minutes": (1, 120)}),
        "fert_drip": MappingProxyType({"minutes": (1, 120), **_RINSE}),
        "mister": MappingProxyType({key: BOUNDS_BY_KIND["mister"][key] for key in ("on_s", "count", "gap_s")}),
        "fert_mister": MappingProxyType(
            {**{key: BOUNDS_BY_KIND["fert_mister"][key] for key in ("on_s", "count", "gap_s")}, **_RINSE}
        ),
        # #1343 follow-up: fog runs once, 1-30 min of continuous fog (the firmware's irr_fog_run_args_in_bounds).
        "fog": MappingProxyType({"minutes": (1, 30)}),
    }
)
# Line caps (irrigation §4.2): 0 = no cap; vpd_reserve_min is also at most daily_cap_min.
LINE_BOUNDS: Mapping[str, _Range] = MappingProxyType({"daily_cap_min": (0, 480), "vpd_reserve_min": (0, 480)})

# The three irrigation_site registry rows and the record header fields they fill.
SITE_PARAM_FIELDS: Mapping[str, str] = MappingProxyType(
    {
        "mister_daily_volume_max_gal": "saf4_gal",
        "sched_mist_rh_max_pct": "rh_ceiling_pct",
        "climate_yield_min": "climate_yield_min",
    }
)

# Agenda notes on planned occurrences (skipped ones carry REASON_WORDS).
CAP_NOTE = "ends at the daily cap"
DEGRADED_ONLY_NOTE = "only while VPD sensing is degraded"

_KIND_WORDS: Mapping[str, str] = MappingProxyType(
    {
        "drip": "drip",
        "fert_drip": "fertilizer drip",
        "mister": "mister",
        "fert_mister": "fertilizer mister",
        "master": "fertilizer master valve",
        "fog": "fog",
    }
)
_UNITS: Mapping[str, str] = MappingProxyType(
    {
        "on_s": " s",
        "gap_s": " s",
        "prewet_s": " s",
        "flush_s": " s",
        "count": " pulses",
        "minutes": " min",
        "daily_cap_min": " min",
        "vpd_reserve_min": " min",
    }
)
_CLOCK_TEXT = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
_START_API_FIELDS = frozenset({"kind", "clock", "offset_min"})


def _whole(value: object) -> object:
    """Snap a fractional number to a whole one, half to even (#1145's ``snap``); type errors stay pydantic's."""
    if isinstance(value, bool):
        raise ValueError("expected a whole number, not a bool")
    if isinstance(value, float):
        return int(snap(value, 1.0))  # raises ValueError on NaN or infinity
    return value


_Whole = Annotated[int, BeforeValidator(_whole)]


def weekday_mask(days: Iterable[str]) -> int:
    """The record's weekday mask: bit 0 = Monday, from ``ScheduleDays`` names."""
    mask = 0
    for day in days:
        if day not in WEEKDAYS:
            raise ValueError(f"unknown weekday {day!r}; expected one of {', '.join(WEEKDAYS)}")
        mask |= 1 << WEEKDAYS.index(day)
    return mask


def days_from_mask(mask: int) -> frozenset[str]:
    """The ``ScheduleDays`` names in a weekday mask (1-127, bit 0 = Monday)."""
    if isinstance(mask, bool) or not isinstance(mask, int) or not 1 <= mask <= 0x7F:
        raise ValueError(f"weekday mask {mask!r} is outside 1..127")
    return frozenset(day for bit, day in enumerate(WEEKDAYS) if mask >> bit & 1)


def days_to_api(days: Iterable[str]) -> list[str]:
    """The OpenAPI ``ScheduleDays`` list, Monday first."""
    chosen = set(days)
    return [day for day in WEEKDAYS if day in chosen]


class Start(BaseModel):
    """A schedule's start: ``clock`` with ``minute`` the local minute of the day (0-1439), or
    ``sunrise``, ``solar_noon`` or ``sunset`` with ``minute`` the offset from it (−240 to 240).

    A fractional minute snaps to a whole one. The range is checked on submit by
    ``validate_schedule``, which refuses it as ``out_of_range`` with its bounds. ``minute`` has no
    default, so a clock start never silently means midnight; the OpenAPI shape, where a solar
    ``offset_min`` defaults to 0, goes through ``from_api``.
    """

    model_config = _FROZEN

    kind: StartKind
    minute: _Whole

    @property
    def start_kind(self) -> int:
        """The record's ``start_kind`` code (``START_KIND``)."""
        return START_KIND[self.kind]

    @classmethod
    def from_api(cls, start: Mapping[str, Any]) -> Start:
        """From the OpenAPI ``ScheduleStart``: ``{kind: clock, clock: "HH:MM"}`` or ``{kind, offset_min}``.

        ``offset_min`` defaults to 0; a clock start may carry it only as 0 or null. Raises
        ``ValueError`` for any other shape (an unknown field or kind, a malformed clock, a clock on a
        solar start); an offset outside −240 to 240 is left to ``validate_schedule``.
        """
        if not isinstance(start, Mapping):
            raise TypeError(f"ScheduleStart must be a mapping, not {type(start).__name__}")
        unknown = set(start) - _START_API_FIELDS
        if unknown:
            raise ValueError(f"unknown ScheduleStart field(s): {', '.join(sorted(map(str, unknown)))}")
        kind, clock, offset = start.get("kind"), start.get("clock"), start.get("offset_min")
        if kind == "clock":
            if offset not in (None, 0):
                raise ValueError("a clock start takes clock (HH:MM), not offset_min")
            match = _CLOCK_TEXT.fullmatch(clock) if isinstance(clock, str) else None
            if match is None:
                raise ValueError(f"clock must be local HH:MM from 00:00 to 23:59, not {clock!r}")
            return cls(kind="clock", minute=int(match[1]) * 60 + int(match[2]))
        if isinstance(kind, str) and kind in START_KIND:
            if clock is not None:
                raise ValueError(f"a {kind} start takes offset_min, not clock")
            return cls(kind=kind, minute=0 if offset is None else offset)
        raise ValueError(f"unknown start kind {kind!r}; expected one of {', '.join(START_KIND)}")

    def to_api(self) -> dict[str, Any]:
        """The OpenAPI ``ScheduleStart``."""
        if self.kind == "clock":
            hour, minute = divmod(self.minute, 60)
            return {"kind": "clock", "clock": f"{hour:02d}:{minute:02d}"}
        return {"kind": self.kind, "offset_min": self.minute}


class ScheduleSpec(BaseModel):
    """One schedule: an ``irrigation_slot`` row (M3, #1153). The API calls it a schedule, the device a slot.

    ``slot_id`` ``None`` is a create: M3's never-reused sequence assigns the id on insert. ``days``
    takes the OpenAPI ``ScheduleDays`` names (or a weekday mask, bit 0 = Monday) and ``start`` a
    ``Start`` or the OpenAPI ``ScheduleStart``. Fractional seconds snap to whole ones. The bounds per
    line kind are checked on submit (``validate_schedule``), so a value outside them is held here
    and refused there with ``out_of_range``, its bounds and the stored value.
    """

    model_config = _FROZEN

    slot_id: Annotated[int, Field(ge=1, le=0xFFFF)] | None = None
    line_id: LineId
    enabled: StrictBool = True
    days: Annotated[frozenset[Weekday], Field(min_length=1)]
    start: Start
    on_s: _Whole
    count: _Whole = 1
    gap_s: _Whole = 0
    prewet_s: _Whole = 0
    flush_s: _Whole = 0
    degraded_only: StrictBool = False
    label: Annotated[str, Field(max_length=60)] | None = None
    manual: StrictBool = False

    @field_validator("days", mode="before")
    @classmethod
    def _days_from_mask(cls, value: object) -> object:
        if isinstance(value, int) and not isinstance(value, bool):
            return days_from_mask(value)
        return value

    @field_validator("start", mode="before")
    @classmethod
    def _start_from_api(cls, value: object) -> object:
        # Only the row shape ({kind, minute}) skips from_api, so {"kind": "clock"} with no clock is
        # refused rather than read as midnight.
        if isinstance(value, Mapping) and "minute" not in value:
            return Start.from_api(value)
        return value

    @property
    def weekday_mask(self) -> int:
        return weekday_mask(self.days)

    def with_changes(self, changes: Mapping[str, Any]) -> ScheduleSpec:
        """This schedule with ``changes`` applied (an edit's fields, API or row shaped), validated and
        snapped like a new one; ``model_copy(update=...)`` would skip both."""
        return type(self).model_validate(self.model_dump() | dict(changes))

    @property
    def open_s(self) -> int:
        """Valve-open seconds per occurrence on the schedule's own line (``on_s`` × ``count``)."""
        return self.on_s * self.count

    @property
    def minutes(self) -> float:
        """Valve-open minutes per occurrence on the schedule's own line. A fertilizer run's prewet and
        flush run on its clean partner and are not included."""
        return self.open_s / 60

    def to_api(self) -> dict[str, Any]:
        """The OpenAPI ``ScheduleInput`` fields, with ``slot_id`` once stored."""
        out: dict[str, Any] = {} if self.slot_id is None else {"slot_id": self.slot_id}
        out |= {"line_id": self.line_id}
        if self.label is not None:
            out["label"] = self.label
        return out | {
            "enabled": self.enabled,
            "days": days_to_api(self.days),
            "start": self.start.to_api(),
            "on_s": self.on_s,
            "count": self.count,
            "gap_s": self.gap_s,
            "prewet_s": self.prewet_s,
            "flush_s": self.flush_s,
            "degraded_only": self.degraded_only,
            "manual": self.manual,
        }

    @classmethod
    def from_row(cls, row: Any) -> ScheduleSpec:
        """From an ``irrigation_slot`` row (a mapping or an asyncpg record): ``days`` is its weekday
        mask and ``start_kind`` and ``start_min`` its start. Attribution columns are ignored."""
        data = dict(row)
        return cls(
            slot_id=data["slot_id"],
            line_id=data["line_id"],
            enabled=data["enabled"],
            days=data["days"],
            start=Start(kind=data["start_kind"], minute=data["start_min"]),
            on_s=data["on_s"],
            count=data["count"],
            gap_s=data["gap_s"],
            prewet_s=data["prewet_s"],
            flush_s=data["flush_s"],
            degraded_only=data["degraded_only"],
            label=data.get("label"),
            manual=data.get("manual", False),
        )


class LineSpec(BaseModel):
    """One line: the ``irrigation_line`` fields (M3, #1153) the compiler, the agenda and the checks use.

    ``kind``, ``device_idx`` and ``clean_partner`` default to the fixed topology of irrigation §3 and
    must match it. ``vpd_member`` and ``schedule_member`` exist on L04, L07 and L09 only (null
    elsewhere; null reads as off); ``vpd_reserve_min`` null means 0 and ``daily_cap_min`` 0 means no
    cap. The cap bounds are checked on submit (``validate_line``).
    """

    model_config = _FROZEN

    line_id: LineId
    kind: LineKind
    device_idx: Annotated[int, Field(ge=0, le=PROGRAM_LINE_COUNT - 1)]
    clean_partner: LineId | None = None
    reserved: StrictBool = False
    enabled: StrictBool = False
    vpd_member: StrictBool | None = None
    schedule_member: StrictBool | None = None
    daily_cap_min: _Whole = 0
    vpd_reserve_min: _Whole | None = None
    paused_until: AwareDatetime | None = None
    manual: StrictBool = False

    @model_validator(mode="before")
    @classmethod
    def _topology_defaults(cls, data: object) -> object:
        if isinstance(data, Mapping) and isinstance(data.get("line_id"), str) and data["line_id"] in LINE_KIND:
            line_id = data["line_id"]
            defaults = {
                "kind": LINE_KIND[line_id],
                "device_idx": DEVICE_IDX[line_id],
                "clean_partner": CLEAN_PARTNER.get(line_id),
            }
            return defaults | dict(data)
        return data

    @model_validator(mode="after")
    def _fixed_topology(self) -> LineSpec:
        fixed = (LINE_KIND[self.line_id], DEVICE_IDX[self.line_id], CLEAN_PARTNER.get(self.line_id))
        if (self.kind, self.device_idx, self.clean_partner) != fixed:
            raise ValueError(
                f"{self.line_id} is kind {fixed[0]}, device_idx {fixed[1]}, clean partner {fixed[2]} "
                f"(irrigation §3), not {self.kind}, {self.device_idx}, {self.clean_partner}"
            )
        if self.line_id not in MISTER_MASK_BIT and (self.vpd_member or self.schedule_member):
            raise ValueError(f"vpd_member and schedule_member exist on L04, L07 and L09 only, not {self.line_id}")
        return self

    def with_changes(self, changes: Mapping[str, Any]) -> LineSpec:
        """This line with ``changes`` applied (a line update's fields), validated like a new one."""
        return type(self).model_validate(self.model_dump() | dict(changes))

    @property
    def schedule_budget_min(self) -> int | None:
        """Daily minutes left to schedules: the cap, less the VPD reserve on a mister (L04, L07, L09,
        whose reserve holds zone minutes back for VPD pulses); ``None`` with no cap (0)."""
        if self.daily_cap_min == 0:
            return None
        reserve = (self.vpd_reserve_min or 0) if self.kind == "mister" else 0
        return max(self.daily_cap_min - reserve, 0)

    @classmethod
    def from_row(cls, row: Any) -> LineSpec:
        """From an ``irrigation_line`` row (a mapping or an asyncpg record); other columns are ignored."""
        data = dict(row)
        return cls.model_validate({key: value for key, value in data.items() if key in cls.model_fields})


class SiteValues(BaseModel):
    """The program header's three site values: the ``irrigation_site`` registry rows (irrigation §4.1)."""

    model_config = _FROZEN

    saf4_gal: Annotated[int, Field(strict=True, ge=100, le=600)]
    rh_ceiling_pct: Annotated[int, Field(strict=True, ge=75, le=98)]
    climate_yield_min: Annotated[int, Field(strict=True, ge=5, le=30)]


def site_values(rows: Iterable[Any] = ()) -> SiteValues:
    """The site values from ``v_desired_state_current`` rows (each with ``param`` and ``value``).

    Rows for other params are ignored. When a param has several rows the newest wins (by
    ``created_at``, then ``id``; rows without ``created_at`` count as older, in the order given). A
    param with no row, a released one (``value`` null) or a non-finite value falls back to its
    registry default; a value snaps to the registry step, inside the registry bounds
    (``snap_setting``).
    """
    newest: dict[str, tuple[tuple[Any, ...], Any]] = {}
    for position, row in enumerate(rows):
        data = dict(row)
        param = data.get("param")
        if param not in IRRIGATION_SITE_PARAMS:
            continue
        created_at, row_id = data.get("created_at"), data.get("id")
        order = (1, created_at, row_id if row_id is not None else -1, position) if created_at else (0, position)
        if param not in newest or order > newest[param][0]:
            newest[param] = (order, data.get("value"))
    fields: dict[str, int] = {}
    for param, field in SITE_PARAM_FIELDS.items():
        spec = REGISTRY[param]
        value = newest[param][1] if param in newest else None
        number = spec.default if value is None else float(value)
        if not math.isfinite(number):
            number = spec.default
        fields[field] = int(snap_setting(param, number))
    return SiteValues(**fields)


# ── Refusals ─────────────────────────────────────────────────────────────────

RefusalCode = Literal["unknown_target", "out_of_range", "too_close", "too_many_schedules"]
_REFUSAL_TITLES: Mapping[str, str] = MappingProxyType(
    {
        "unknown_target": "Unknown target",
        "out_of_range": "Out of range",
        "too_close": "Too close",
        "too_many_schedules": "Too many schedules",
    }
)


class Refusal(BaseModel):
    """Why a submit was refused, in the OpenAPI ``Problem`` shape (422): a stable ``code``, the
    request ``field`` at fault, its bounds, the stored or device value as ``current``, the
    ``detail`` in words and ``retryable`` (false for every code here)."""

    model_config = _FROZEN

    code: RefusalCode
    field: str | None = None
    min: int | None = None
    max: int | None = None
    current: int | float | str | bool | None = None
    detail: str
    retryable: bool = False

    def problem(self) -> dict[str, Any]:
        """The ``application/problem+json`` body: ``type``, ``title``, ``status`` 422 and the set fields."""
        body: dict[str, Any] = {
            "type": "about:blank",
            "title": _REFUSAL_TITLES[self.code],
            "status": 422,
            "code": self.code,
            "detail": self.detail,
            "retryable": self.retryable,
        }
        for key in ("field", "min", "max", "current"):
            value = getattr(self, key)
            if value is not None:
                body[key] = value
        return body


def _out_of_range(field: str, lo: int, hi: int, current: int | str | bool | None, detail: str) -> Refusal:
    return Refusal(code="out_of_range", field=field, min=lo, max=hi, current=current, detail=detail)


def _range_words(field: str, lo: int, hi: int) -> str:
    unit = " pulse" if field == "count" and hi == 1 else _UNITS.get(field, "")
    return f"{lo}{unit}" if lo == hi else f"{lo}-{hi}{unit}"


def _lines_by_id(lines: Iterable[LineSpec]) -> dict[str, LineSpec]:
    by_id: dict[str, LineSpec] = {}
    for line in lines:
        if not isinstance(line, LineSpec):
            raise TypeError(f"expected LineSpec rows (LineSpec.from_row), not {type(line).__name__}")
        if line.line_id in by_id:
            raise ValueError(f"line {line.line_id} is listed twice")
        by_id[line.line_id] = line
    return by_id


def _schedule_refusal(spec: ScheduleSpec, line: LineSpec | None, before: ScheduleSpec | None = None) -> Refusal | None:
    """Submit steps 1 and 2 for one schedule: a line that takes schedules, then each field in its bounds."""
    if line is None:
        return Refusal(code="unknown_target", field="line_id", detail=f"line {spec.line_id} is not known")
    bounds = BOUNDS_BY_KIND.get(line.kind)
    if bounds is None:
        return Refusal(
            code="unknown_target",
            field="line_id",
            detail=(
                f"{line.line_id} ({_KIND_WORDS[line.kind]}) takes no schedules: only L01, L02 and L04-L10 do "
                "(L03 opens with fertilizer runs; L11 fog is climate only)"
            ),
        )
    lo, hi = START_RANGE[spec.start.start_kind]
    if not lo <= spec.start.minute <= hi:
        if spec.start.kind == "clock":
            field, current = "start.clock", None if before is None else before.start.to_api().get("clock")
            detail = "a clock start must be from 00:00 to 23:59 local"
        else:
            field, current = "start.offset_min", None if before is None else before.start.to_api().get("offset_min")
            detail = f"a {spec.start.kind} start must be within {lo} to {hi} min of it"
        return _out_of_range(field, lo, hi, current, detail)
    kind_words = _KIND_WORDS[line.kind]
    for field, (lo, hi) in bounds.items():
        value = getattr(spec, field)
        if not lo <= value <= hi:
            current = None if before is None else getattr(before, field)
            detail = f"{field} must be {_range_words(field, lo, hi)} on a {kind_words} line ({line.line_id})"
            return _out_of_range(field, lo, hi, current, detail)
    if spec.degraded_only and DEVICE_IDX[line.line_id] not in DEGRADED_ONLY_IDX:
        current = None if before is None else before.degraded_only
        return _out_of_range(
            "degraded_only", 0, 0, current, f"degraded_only is allowed on L04, L07 and L09 only, not {line.line_id}"
        )
    return None


def validate_line(line: LineSpec, *, before: LineSpec | None = None) -> Refusal | None:
    """The line caps on submit (API-IRR-08): ``daily_cap_min`` 0-480 (0 = no cap) and
    ``vpd_reserve_min`` 0 to ``daily_cap_min``. ``before``, the stored line, gives ``current``."""
    lo, hi = LINE_BOUNDS["daily_cap_min"]
    if not lo <= line.daily_cap_min <= hi:
        current = None if before is None else before.daily_cap_min
        return _out_of_range("daily_cap_min", lo, hi, current, f"daily_cap_min must be {lo}-{hi} min (0 = no cap)")
    reserve = 0 if line.vpd_reserve_min is None else line.vpd_reserve_min
    if not 0 <= reserve <= line.daily_cap_min:
        current = None if before is None else before.vpd_reserve_min
        return _out_of_range(
            "vpd_reserve_min",
            0,
            line.daily_cap_min,
            current,
            f"vpd_reserve_min must be 0-{line.daily_cap_min} min, at most the daily cap",
        )
    return None


# ── Run-now arguments (the OpenAPI RunRequest) ───────────────────────────────


class DripRunArgs(BaseModel):
    """Run-now on a drip (L01, L05): valve-open minutes."""

    model_config = _FROZEN

    minutes: _Whole


class FertDripRunArgs(BaseModel):
    """Run-now on a fertilizer drip (L02, L06): feed minutes, with a prewet and a flush on the clean partner."""

    model_config = _FROZEN

    minutes: _Whole
    prewet_s: _Whole
    flush_s: _Whole


class MisterRunArgs(BaseModel):
    """Run-now on a mister (L04, L07, L09): a one-off window of ``count`` pulses of ``on_s``, ``gap_s`` apart."""

    model_config = _FROZEN

    on_s: _Whole
    count: _Whole
    gap_s: _Whole


class FertMisterRunArgs(BaseModel):
    """Run-now on a fertilizer mister (L08, L10): it pulses like any mister, with a prewet and a flush
    on the clean partner."""

    model_config = _FROZEN

    on_s: _Whole
    count: _Whole
    gap_s: _Whole
    prewet_s: _Whole
    flush_s: _Whole


class FogRunArgs(BaseModel):
    """Run-now on fog (L11; #1343 follow-up): one continuous fog of ``minutes``, held by the climate rails."""

    model_config = _FROZEN

    minutes: _Whole


RunArgs = DripRunArgs | FertDripRunArgs | MisterRunArgs | FertMisterRunArgs | FogRunArgs
RUN_ARGS_BY_KIND: Mapping[str, type[RunArgs]] = MappingProxyType(
    {
        "drip": DripRunArgs,
        "fert_drip": FertDripRunArgs,
        "mister": MisterRunArgs,
        "fert_mister": FertMisterRunArgs,
        "fog": FogRunArgs,
    }
)


def run_args(kind: str, body: Mapping[str, Any]) -> RunArgs:
    """Parse a ``RunRequest`` body for a line kind: exactly that kind's fields (``reason`` is the activity row's).

    Raises ``ValidationError`` when one of the kind's fields is missing or not a whole number, or
    another kind's field is present (``loc`` names it, for the 400 ``invalid_request``), and
    ``ValueError`` for a kind that takes no runs (``validate_run`` refuses those lines as
    ``unknown_target``). The bounds are ``validate_run``'s.
    """
    model = RUN_ARGS_BY_KIND.get(kind)
    if model is None:
        raise ValueError(f"a {kind} line takes no runs")
    parsed: RunArgs = model.model_validate({key: value for key, value in body.items() if key != "reason"})
    return parsed


def validate_run(line: LineSpec, args: RunArgs) -> Refusal | None:
    """Run-now bounds on submit (API-IRR-06): ``unknown_target`` on a line that takes no runs (L03),
    then ``out_of_range`` with ``field``, ``min`` and ``max`` per ``RUN_BOUNDS_BY_KIND``.

    The run refusals that depend on live state (``line_disabled``, ``line_busy``, ``cap_reached``
    and the rest, irrigation §4.7) are the services'.
    """
    bounds = RUN_BOUNDS_BY_KIND.get(line.kind)
    if bounds is None:
        return Refusal(
            code="unknown_target",
            field="line_id",
            detail=f"{line.line_id} ({_KIND_WORDS[line.kind]}) takes no runs: L03 opens only with fertilized runs",
        )
    expected = RUN_ARGS_BY_KIND[line.kind]
    if not isinstance(args, expected):
        raise TypeError(f"{line.line_id} takes {expected.__name__}, not {type(args).__name__}")
    for field, (lo, hi) in bounds.items():
        value = getattr(args, field)
        if not lo <= value <= hi:
            detail = f"{field} must be {_range_words(field, lo, hi)} for a run on a {_KIND_WORDS[line.kind]} line ({line.line_id})"
            return _out_of_range(field, lo, hi, None, detail)
    return None


# ── Local time and solar starts ──────────────────────────────────────────────


def local_tz() -> ZoneInfo:
    """America/Denver, resolved on first use (``ZoneInfo`` caches it), so importing this module, as
    every ``verdify_schemas`` consumer does, needs no time-zone data."""
    return ZoneInfo(LOCAL_TZ_NAME)


def utc_offset_min(day: date) -> int:
    """America/Denver's UTC offset in minutes at local noon on ``day`` (−360 MDT, −420 MST)."""
    offset = datetime.combine(day, time(12), tzinfo=local_tz()).utcoffset()
    if offset is None:  # pragma: no cover - ZoneInfo always answers
        raise ValueError(f"no UTC offset for {day}")
    return int(offset.total_seconds()) // 60


@lru_cache(maxsize=1024)
def solar_times(day: date) -> SolarTimes:
    """Sunrise, solar noon and sunset on one America/Denver date, in local minutes:
    ``compute_solar_times`` with the date's UTC offset at local noon."""
    return compute_solar_times(day.timetuple().tm_yday, day.year, utc_offset_min=utc_offset_min(day))


def _at_local_offset(moment: datetime) -> datetime:
    """``moment`` at the fixed America/Denver offset in force then: the same instant and wall time, but
    safe to compare with any aware datetime (PEP 495 makes a ``ZoneInfo`` time in the repeated
    fall-back hour unequal to every other zone's)."""
    offset = moment.astimezone(local_tz()).utcoffset()
    if offset is None:  # pragma: no cover - ZoneInfo always answers
        raise ValueError(f"no UTC offset at {moment}")
    return moment.astimezone(timezone(offset))


def local_start(day: date, minute: int) -> datetime:
    """The instant at which the America/Denver wall clock of ``day`` first reads ``minute``, aware at
    the local offset then in force.

    ``minute`` past 1439 (or below 0) falls on the next (or previous) day. A time the clock repeats
    (the fall-back hour) is its first occurrence; a time it skips (the spring-forward gap) is the
    instant the clock jumps past it.
    """
    extra_days, minute = divmod(minute, 1440)
    naive = datetime.combine(day + timedelta(days=extra_days), time(minute // 60, minute % 60))
    instant = naive.replace(tzinfo=local_tz()).astimezone(UTC)  # fold 0: the first of a repeated time
    if instant.astimezone(local_tz()).replace(tzinfo=None) != naive:
        # A skipped time: fold 0 lands past the gap; step back to the instant the clock jumps over it.
        step = timedelta(minutes=1)
        while (instant - step).astimezone(local_tz()).replace(tzinfo=None) >= naive:
            instant -= step
    return _at_local_offset(instant)


def _start_minute(start: Start, day: date) -> int:
    if start.kind == "clock":
        return start.minute
    times = solar_times(day)
    base = {"sunrise": times.sunrise_min, "solar_noon": times.solar_noon_min, "sunset": times.sunset_min}
    return base[start.kind] + start.minute


def occurrence_start(start: Start, day: date) -> datetime:
    """When a schedule's occurrence on local date ``day`` starts (``local_start`` of its minute)."""
    return local_start(day, _start_minute(start, day))


def _starts(spec: ScheduleSpec, first: date, count: int) -> list[tuple[float, datetime]]:
    """(epoch seconds, start) of each occurrence on the ``count`` dates from ``first``, in order."""
    starts = []
    for offset in range(count):
        day = first + timedelta(days=offset)
        if WEEKDAYS[day.weekday()] in spec.days:
            start = occurrence_start(spec.start, day)
            starts.append((start.timestamp(), start))
    starts.sort(key=lambda item: item[0])
    return starts


def _closest(
    ours: Sequence[tuple[float, datetime]], theirs: Sequence[tuple[float, datetime]]
) -> tuple[float, datetime, datetime] | None:
    """The smallest gap between an instant of ``ours`` and one of ``theirs``, both sorted, and the pair."""
    best: tuple[float, datetime, datetime] | None = None
    i = j = 0
    while i < len(ours) and j < len(theirs):
        gap = abs(ours[i][0] - theirs[j][0])
        if best is None or gap < best[0]:
            best = (gap, ours[i][1], theirs[j][1])
        if ours[i][0] < theirs[j][0]:
            i += 1
        else:
            j += 1
    return best


def _when(start: datetime) -> str:
    return f"{WEEKDAYS[start.weekday()].title()} {start:%Y-%m-%d %H:%M}"


def validate_schedule(
    candidate: ScheduleSpec,
    existing: Sequence[ScheduleSpec],
    lines: Sequence[LineSpec],
    *,
    today: date | None = None,
) -> Refusal | None:
    """Check a schedule create or edit on submit (irrigation §4.3); ``None`` accepts it.

    ``existing`` is every stored schedule (enabled or not) and ``lines`` the stored lines; #1156
    reads both under the irrigation advisory lock. ``candidate.slot_id`` ``None`` is a create; an
    edit names a stored slot and carries every field after the edit. ``today`` (America/Denver,
    default the current date) starts the spacing window. In order:

    1. An unknown slot or line, or a line that takes no schedules (L03, L11): ``unknown_target``.
    2. A start or field outside its bounds for the line's kind (``BOUNDS_BY_KIND``; a clock start
       0-1439, a solar offset −240 to 240; ``degraded_only`` on L04, L07 and L09 only):
       ``out_of_range`` with ``field``, ``min``, ``max`` and, on an edit, the stored value as
       ``current``. Fractional seconds were snapped when the spec was built.
    3. An enabled candidate whose occurrences, resolved to local start instants over the 366 days
       from ``today``, come within ``MIN_SPACING_MIN`` of another enabled schedule's on the same line,
       across midnight included: ``too_close``, naming the other slot (D-IRP-06).
    4. A create when ``MAX_SCHEDULES`` are stored: ``too_many_schedules`` with ``current`` and ``max``.

    Nothing else refuses: a schedule on a disabled line, a mist schedule while its zone is not a
    member, and runs past a cap are stored and settle at run time.
    """
    stored = {slot.slot_id: slot for slot in existing}
    before = None
    if candidate.slot_id is not None:
        before = stored.get(candidate.slot_id)
        if before is None:
            return Refusal(
                code="unknown_target", field="slot_id", detail=f"schedule {candidate.slot_id} does not exist"
            )
    by_id = _lines_by_id(lines)
    refusal = _schedule_refusal(candidate, by_id.get(candidate.line_id), before)
    if refusal is not None:
        return refusal

    if candidate.enabled:
        first = today if today is not None else datetime.now(local_tz()).date()
        ours = _starts(candidate, first, 366)
        spacing = MIN_SPACING_MIN * 60
        others = (
            other
            for other in sorted(existing, key=lambda slot: slot.slot_id or 0)
            if other.enabled and other.line_id == candidate.line_id and other.slot_id != candidate.slot_id
        )
        for other in others:
            # One day either side, so starts across midnight at the window's ends are compared too.
            closest = _closest(ours, _starts(other, first - timedelta(days=1), 368))
            if closest is not None and closest[0] < spacing:
                gap, mine, theirs = closest
                return Refusal(
                    code="too_close",
                    field="start",
                    detail=(
                        f"starts {int(gap // 60)} min from schedule {other.slot_id} on {candidate.line_id} "
                        f"({_when(mine)} and {_when(theirs)}); starts on one line must be at least "
                        f"{MIN_SPACING_MIN} min apart"
                    ),
                )

    if candidate.slot_id is None and len(existing) >= MAX_SCHEDULES:
        return Refusal(
            code="too_many_schedules",
            current=len(existing),
            max=MAX_SCHEDULES,
            detail=f"{len(existing)} schedules are stored and the controller holds at most {MAX_SCHEDULES}",
        )
    return None


def bounds_by_kind() -> dict[str, dict[str, dict[str, int]]]:
    """``BOUNDS_BY_KIND`` as the OpenAPI ``ScheduleAgenda.bounds`` map: kind → field → {min, max}."""
    return {
        kind: {field: {"min": lo, "max": hi} for field, (lo, hi) in fields.items()}
        for kind, fields in BOUNDS_BY_KIND.items()
    }


# ── Compiler ─────────────────────────────────────────────────────────────────


class CompiledProgram(BaseModel):
    """A compiled program: the 584 B record, its SHA-256 (64 lowercase hex), the slot ids on the
    controller in record order, and the enabled schedules left off it."""

    model_config = _FROZEN

    record: Annotated[bytes, Field(min_length=PROGRAM_RECORD_LEN, max_length=PROGRAM_RECORD_LEN)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    slot_ids: tuple[int, ...] = ()
    not_on_controller: tuple[int, ...] = ()

    @property
    def hash16(self) -> str:
        """The first 16 hex of ``sha256``: what ``irr_program_id`` reports once the program is applied."""
        return self.sha256[:16]


def _epoch_s(moment: datetime | None) -> int:
    """UTC epoch seconds for the record, rounded up so a pause never ends early; null is 0."""
    if moment is None:
        return 0
    return min(max(math.ceil(moment.timestamp()), 0), 0xFFFF_FFFF)


def _record_slot(spec: ScheduleSpec) -> RecordSlot:
    if spec.slot_id is None:
        raise ValueError("compile_program() takes stored schedules; slot_id is None")
    return RecordSlot(
        slot_id=spec.slot_id,
        line=DEVICE_IDX[spec.line_id],
        weekday_mask=spec.weekday_mask,
        start_kind=spec.start.start_kind,
        degraded_only=spec.degraded_only,
        start=spec.start.minute,
        on_s=spec.on_s,
        gap_s=spec.gap_s,
        prewet_s=spec.prewet_s,
        flush_s=spec.flush_s,
        count=spec.count,
    )


def compile_program(lines: Sequence[LineSpec], slots: Sequence[ScheduleSpec], site: SiteValues) -> CompiledProgram:
    """Compile the stored lines, schedules and site values into the program record (irrigation §5).

    ``lines`` must hold all 11 lines. The header takes ``site``, the enabled mask (bit =
    ``device_idx``) and the mister masks (``vpd_member``, ``schedule_member``; ``MISTER_MASK_BIT``);
    each line its cap, VPD reserve (null 0) and pause (UTC epoch seconds, null 0). Only enabled
    schedules are compiled, by ascending ``slot_id``: the 24 lowest go on the controller and the rest
    are listed in ``not_on_controller``, as is any schedule the controller would refuse (a field
    outside its line kind's bounds, which only a row written around ``validate_schedule`` can hold),
    so one bad row never blocks the program. Deterministic: the same rows, in any order, always
    give the same bytes and SHA-256.
    """
    if not isinstance(site, SiteValues):
        raise TypeError(f"site must be SiteValues (site_values()), not {type(site).__name__}")
    by_id = _lines_by_id(lines)
    missing = [line_id for line_id in LINE_IDS if line_id not in by_id]
    if missing:
        raise ValueError(f"compile_program() needs all 11 lines; missing {', '.join(missing)}")

    enabled = sorted((slot for slot in slots if slot.enabled), key=lambda slot: slot.slot_id or 0)
    slot_ids = [slot.slot_id for slot in enabled]
    if None in slot_ids:
        raise ValueError("compile_program() takes stored schedules; a slot_id is None")
    if len(set(slot_ids)) != len(slot_ids):
        raise ValueError("compile_program() got one slot_id twice")
    valid = [slot for slot in enabled if _schedule_refusal(slot, by_id[slot.line_id]) is None]
    on_controller = valid[:MAX_SCHEDULES]
    kept = {slot.slot_id for slot in on_controller}

    record = ProgramRecord(
        saf4_gal=site.saf4_gal,
        rh_ceiling_pct=site.rh_ceiling_pct,
        climate_yield_min=site.climate_yield_min,
        enabled_mask=sum(1 << line.device_idx for line in by_id.values() if line.enabled),
        vpd_member_mask=sum(1 << bit for line_id, bit in MISTER_MASK_BIT.items() if by_id[line_id].vpd_member),
        schedule_member_mask=sum(
            1 << bit for line_id, bit in MISTER_MASK_BIT.items() if by_id[line_id].schedule_member
        ),
        lines=tuple(
            RecordLine(
                daily_cap_min=by_id[line_id].daily_cap_min,
                vpd_reserve_min=by_id[line_id].vpd_reserve_min or 0,
                paused_until=_epoch_s(by_id[line_id].paused_until),
            )
            for line_id in LINE_IDS
        ),
        slots=tuple(_record_slot(slot) for slot in on_controller),
    )
    data = pack_program(record)
    return CompiledProgram(
        record=data,
        sha256=program_sha256(data),
        slot_ids=tuple(slot_id for slot_id in slot_ids if slot_id in kept),
        not_on_controller=tuple(slot_id for slot_id in slot_ids if slot_id not in kept),
    )


# ── Agenda ───────────────────────────────────────────────────────────────────


class AgendaOccurrence(BaseModel):
    """One planned occurrence: a schedule on one local day (``local_day``, the day its weekday matched;
    a solar start past midnight starts the next morning). ``minutes`` is valve-open minutes on its
    own line and ``pulses`` the pulse count on a mister. A ``skipped`` one carries the
    ``REASON_WORDS`` reason and its code; a ``planned`` one may carry notes (``DEGRADED_ONLY_NOTE``,
    ``CAP_NOTE``) as its ``reason``."""

    model_config = _FROZEN

    slot_id: int
    line_id: LineId
    local_day: date
    planned_start: AwareDatetime
    minutes: float
    pulses: int | None = None
    status: Literal["planned", "skipped"]
    reason: str | None = None
    reason_code: ReasonCode | None = None


class SolarDay(BaseModel):
    """Sunrise, solar noon and sunset on one local day (``ScheduleAgenda.solar``)."""

    model_config = _FROZEN

    date: date
    sunrise: AwareDatetime
    solar_noon: AwareDatetime
    sunset: AwareDatetime


class Agenda(BaseModel):
    """The occurrences of ``first_day`` to ``last_day`` (America/Denver), ordered by planned start,
    and each day's solar times."""

    model_config = _FROZEN

    first_day: date
    last_day: date
    occurrences: tuple[AgendaOccurrence, ...] = ()
    solar: tuple[SolarDay, ...] = ()


def _skip_code(line: LineSpec, slot: ScheduleSpec, start: datetime) -> str | None:
    """Why an occurrence will not run, as a REASON_WORDS code, or ``None``."""
    if not line.enabled:
        return "dis"
    if line.line_id in MISTER_MASK_BIT:
        # A degraded_only slot follows vpd_member (D-IRP-12); every other mist schedule schedule_member.
        if slot.degraded_only:
            if not line.vpd_member:
                return "nvm"
        elif not line.schedule_member:
            return "nsm"
    if line.paused_until is not None and start.timestamp() < line.paused_until.timestamp():
        return "pau"
    return None


def _cap_charges(slot: ScheduleSpec, line: LineSpec) -> list[tuple[str, int]]:
    """Valve-open seconds one occurrence adds to each line's daily cap counter (irrigation §4.5).

    Its own run or pulses count on its own line. A fertilizer run's prewet and flush run on the
    clean partner and count there, as a drip cap counts prewet and flush; a fertilizer mister's
    feed pulses count there too, because a mister zone's duty counts every pulse on the zone.
    """
    charges = [(line.line_id, slot.open_s)]
    if line.clean_partner is not None:
        partner_s = slot.prewet_s + slot.flush_s + (slot.open_s if line.kind == "fert_mister" else 0)
        if partner_s:
            charges.append((line.clean_partner, partner_s))
    return charges


def _solar_day(day: date) -> SolarDay:
    times = solar_times(day)
    return SolarDay(
        date=day,
        sunrise=local_start(day, times.sunrise_min),
        solar_noon=local_start(day, times.solar_noon_min),
        sunset=local_start(day, times.sunset_min),
    )


def agenda(
    lines: Sequence[LineSpec],
    slots: Sequence[ScheduleSpec],
    *,
    today: date,
    days: int = 7,
    past_days: int = 0,
) -> Agenda:
    """The week view (API-IRR-02): ``today`` and the ``days`` - 1 after it (1-7), plus ``past_days``
    before it (0-7), as America/Denver dates.

    Each enabled schedule gives one occurrence per local day on its weekdays, with solar starts
    from ``solar_times`` (that date's UTC offset at local noon). An occurrence is ``skipped`` with
    ``REASON_WORDS``: "line disabled"; "not a schedule member" for a mist schedule whose zone has
    Schedule off, or "not a VPD member" for a ``degraded_only`` one, which follows ``vpd_member``;
    "paused" when it starts before ``paused_until``. A planned one notes "only while VPD sensing is
    degraded" on a ``degraded_only`` schedule and "ends at the daily cap" once the planned minutes
    that local day on a line it waters pass that line's non-zero ``daily_cap_min`` (misters: the cap
    less the VPD reserve). A line's minutes are what its cap counts (irrigation §4.5): its own runs
    and pulses, plus the prewet and flush its fertilizer partner puts on it, and on a mister zone
    that partner's feed pulses too (``_cap_charges``). Past days are planned the same way: the reads
    (#1155) join their outcomes and add who paused. Starts are aware at the local offset in force
    (``local_start``).
    """
    for name, value, lo, hi in (("days", days, 1, 7), ("past_days", past_days, 0, 7)):
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ValueError(f"{name} must be {lo}-{hi}, not {value!r}")
    if isinstance(today, datetime) or not isinstance(today, date):
        raise TypeError(f"today must be a date, not {type(today).__name__}")
    by_id = _lines_by_id(lines)
    window = [today + timedelta(days=offset) for offset in range(-past_days, days)]

    found: list[tuple[float, int, date, datetime, ScheduleSpec, LineSpec]] = []
    for slot in slots:
        if not slot.enabled:
            continue
        if slot.slot_id is None:
            raise ValueError("agenda() takes stored schedules; a slot_id is None")
        line = by_id.get(slot.line_id)
        if line is None:
            raise ValueError(f"schedule {slot.slot_id} is on {slot.line_id}, which is not in lines")
        for day in window:
            if WEEKDAYS[day.weekday()] in slot.days:
                start = occurrence_start(slot.start, day)
                found.append((start.timestamp(), slot.slot_id, day, start, slot, line))
    found.sort(key=lambda item: (item[0], item[1]))

    used_s: dict[tuple[str, date], int] = {}  # valve-open seconds planned per line and local day
    occurrences = []
    for _, slot_id, day, start, slot, line in found:
        code = _skip_code(line, slot, start)
        if code is None:
            notes = [DEGRADED_ONLY_NOTE] if slot.degraded_only else []
            over_cap = False
            for counted, seconds in _cap_charges(slot, line):
                # The controller's daily counters reset at local midnight, so the start's date counts.
                key = (counted, start.date())
                used_s[key] = used_s.get(key, 0) + seconds
                counter = by_id.get(counted)
                budget = None if counter is None else counter.schedule_budget_min
                over_cap = over_cap or (budget is not None and used_s[key] > budget * 60)
            if over_cap:
                notes.append(CAP_NOTE)
            reason = "; ".join(notes) or None
        else:
            reason = REASON_WORDS[code]
        occurrences.append(
            AgendaOccurrence(
                slot_id=slot_id,
                line_id=slot.line_id,
                local_day=day,
                planned_start=start,
                minutes=slot.minutes,
                pulses=slot.count if line.kind in ("mister", "fert_mister") else None,
                status="planned" if code is None else "skipped",
                reason=reason,
                reason_code=code,
            )
        )
    return Agenda(
        first_day=window[0],
        last_day=window[-1],
        occurrences=tuple(occurrences),
        solar=tuple(_solar_day(day) for day in window),
    )


# ── Commands: run-now and stop (VD-64, #1177) ────────────────────────────────

CommandKind = Literal["run", "stop", "clear_fault", "override"]
CommandStatus = Literal["pending", "sent", "failed"]
COMMAND_KINDS: tuple[str, ...] = get_args(CommandKind)
COMMAND_STATUSES: tuple[str, ...] = get_args(CommandStatus)
# A command not sent within this many seconds of its request ends failed ("too old") and is never
# sent, so a queued run never starts late; a Stop's relays that have not read OFF are switched off
# again for at most this long after it was sent (irrigation §6.1).
COMMAND_SEND_WINDOW_S = 120
STOP_RESEND_WINDOW_S = 120
# Before the engine firmware only L01 runs (the btn_wall_clean bridge, irrigation §6.5).
BRIDGE_RUN_LINE = "L01"


class CommandActor(BaseModel):
    """Who asked: the OpenAPI ``Actor`` (``source`` in the R4 vocabulary and the actor's name)."""

    model_config = _FROZEN

    source: Annotated[str, Field(pattern=ACTOR_SOURCE_PATTERN)]
    name: Annotated[str, Field(min_length=1, max_length=ACTOR_NAME_MAX_LENGTH)]


class IrrigationCommandRow(BaseModel):
    """An ``irrigation_command`` row (M3, migration 283): a run, stop, clear-fault or override request.

    The services insert it ``pending``; only the ingestor turns it into a device call, marking it
    ``sent`` (with ``sent_at``) in a committed transaction just before the call, or ``failed`` with
    a ``fail_reason``. ``line_id`` None means every line. ``args`` holds the request as stored plus
    what the ingestor records on it (a bridge Stop's ``relays_on``, ``restore_irrig_enabled`` and
    ``restored``). Idempotency lives on the write's ``activity_log`` row, not here.
    """

    model_config = ConfigDict(extra="ignore")

    command_id: UUID | None = None
    greenhouse_id: str = "vallery"
    kind: CommandKind
    line_id: LineId | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    status: CommandStatus = "pending"
    fail_reason: str | None = None
    source: Annotated[str, Field(pattern=ACTOR_SOURCE_PATTERN)]
    actor: Annotated[str, Field(min_length=1, max_length=ACTOR_NAME_MAX_LENGTH)]
    reason: Annotated[str, Field(max_length=REASON_MAX_LENGTH)] | None = None
    created_at: AwareDatetime | None = None
    sent_at: AwareDatetime | None = None

    @field_validator("args", mode="before")
    @classmethod
    def _args_from_json(cls, value: object) -> object:
        # asyncpg returns jsonb as text unless a codec is set.
        return json.loads(value) if isinstance(value, str | bytes) else value

    @model_validator(mode="after")
    def _status_and_sent_at(self) -> IrrigationCommandRow:
        if self.status == "sent" and self.sent_at is None:
            raise ValueError("a sent command records sent_at")
        if self.status == "pending" and self.sent_at is not None:
            raise ValueError("a pending command has no sent_at")
        return self

    @classmethod
    def from_row(cls, row: Any) -> IrrigationCommandRow:
        """From an ``irrigation_command`` row (a mapping or an asyncpg record)."""
        return cls.model_validate(dict(row))


class Command(BaseModel):
    """The OpenAPI ``Command``: what API-IRR-06 and API-IRR-07 (and MCP ``irrigation_run`` and
    ``irrigation_stop``) answer with 202. Relay edges and ``irr_state``, never this row, show
    whether water started or stopped."""

    model_config = _FROZEN

    command_id: UUID
    kind: CommandKind
    line_id: LineId | None
    args: dict[str, Any] = Field(default_factory=dict)
    status: CommandStatus
    actor: CommandActor
    reason: str | None = None
    created_at: AwareDatetime
    sent_at: AwareDatetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> Command:
        """From a stored ``irrigation_command`` row (validated as ``IrrigationCommandRow`` first)."""
        stored = row if isinstance(row, IrrigationCommandRow) else IrrigationCommandRow.from_row(row)
        if stored.command_id is None or stored.created_at is None:
            raise ValueError("a Command is read from a stored row, with its command_id and created_at")
        return cls(
            command_id=stored.command_id,
            kind=stored.kind,
            line_id=stored.line_id,
            args=stored.args,
            status=stored.status,
            actor=CommandActor(source=stored.source, name=stored.actor),
            reason=stored.reason,
            created_at=stored.created_at,
            sent_at=stored.sent_at,
        )


# ── The ingestor's program and outcome rows (VD-42, #1157; irrigation §6.1 and §6.2) ──


class IrrigationPushRow(BaseModel):
    """An ``irrigation_push`` row (M3, migration 283): one compiled program sent, as the ingestor records it just before
    the device call. A re-push of the same hash updates the row (``pushed_at``, cleared ``reject_code`` and
    ``confirmed_at``); ``confirmed_at`` is set once ``irr_program_id`` reports the hash."""

    model_config = ConfigDict(extra="ignore")

    revision: int | None = None
    greenhouse_id: str = "vallery"
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    bytes: Annotated[bytes, Field(min_length=PROGRAM_RECORD_LEN, max_length=PROGRAM_RECORD_LEN)]
    site_desired_at: AwareDatetime | None = None
    pushed_at: AwareDatetime | None = None
    confirmed_at: AwareDatetime | None = None
    reject_code: RejectCode | None = None

    @model_validator(mode="after")
    def _hash_of_the_record(self) -> IrrigationPushRow:
        if self.sha256 != program_sha256(self.bytes):
            raise ValueError("sha256 is not the record's hash")
        return self


class IrrigationLedgerRow(BaseModel):
    """An ``irrigation_ledger`` row (M3, migration 283): one occurrence stage or run-now, written only by the ingestor.
    ``slot_id`` is None for a run-now (M3's unique key skips those); ``started_at`` is None for one that never opened a
    valve; ``outcome`` is None until ``irr_events`` names it. ``reason`` holds the ``REASON_WORDS`` code."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    greenhouse_id: str = "vallery"
    line_id: LineId
    slot_id: _SlotId | None = None
    local_day: date
    cause: IrrigationCause
    started_at: AwareDatetime | None = None
    ended_at: AwareDatetime | None = None
    open_s: Annotated[int, Field(strict=True, ge=0)] = 0
    pulses: Annotated[int, Field(strict=True, ge=0)] | None = None
    outcome: RunOutcome | None = None
    reason: Annotated[str, Field(max_length=REASON_MAX_LENGTH)] | None = None
    event_seq: _U16 | None = None
    command_id: UUID | None = None
    actor: str | None = None
    recorded_at: AwareDatetime | None = None


class RunClose(BaseModel):
    """A ledger row's closing columns, from the line's relay edges when its run token leaves ``irr_state``."""

    model_config = _FROZEN

    ended_at: AwareDatetime
    open_s: Annotated[int, Field(strict=True, ge=0)]
    pulses: Annotated[int, Field(strict=True, ge=0)]


# ── Line state (VD-40, #1155; irrigation §6.2 and §8.1) ──────────────────────

LineStateCode = Literal["idle", "running", "waiting", "blocked", "paused", "disabled", "unknown"]
# The engine names neither: L03 opens only inside a feed and L11 fog is climate only, so both always read
# their relay, never disabled.
RELAY_ONLY_LINES: frozenset[str] = frozenset({"L03", "L11"})
# M4's line map (migration 281): why a relay ran when only its edge says so. M3's ledger and bridge rules go first.
TOPOLOGY_CAUSE: Mapping[str, IrrigationCause] = MappingProxyType(
    {
        "L01": IrrigationCause.UNKNOWN,
        "L02": IrrigationCause.FEED,
        "L03": IrrigationCause.FEED,
        "L04": IrrigationCause.VPD,
        "L05": IrrigationCause.UNKNOWN,
        "L06": IrrigationCause.UNKNOWN,
        "L07": IrrigationCause.VPD,
        "L08": IrrigationCause.UNKNOWN,
        "L09": IrrigationCause.VPD,
        "L10": IrrigationCause.UNKNOWN,
        "L11": IrrigationCause.VPD,
    }
)
# An L01 ON edge at most this long after a sent bridge command is that command's run (M3, §6.5).
BRIDGE_RUN_WINDOW_S = 30
# A w= token whose run goes by itself once the manifold or L03 frees, nobody is seen (US-WAT-16: "Waiting: someone
# inside") or VPD sensing degrades reads waiting; any other code names a rail that holds the run (a limit, a cap, a
# sensor fault, the watchdog's back-off or latch) and reads blocked.
WAITING_REASONS: frozenset[str] = frozenset({"man", "l03", "occ", "vok"})
_VPD_ZONE_OF_LINE: Mapping[str, str] = MappingProxyType({"L04": "center", "L07": "south", "L09": "west"})


class LineState(BaseModel):
    """The OpenAPI ``LineState``: what a line is doing now and how old that knowledge is."""

    model_config = _FROZEN

    code: LineStateCode
    cause: IrrigationCause | None = None
    slot_id: int | None = None
    reason: str | None = None
    ends_at: AwareDatetime | None = None
    observed_at: AwareDatetime | None = None


class LineStateFields(BaseModel):
    """``line_state()``'s answer: the ``IrrigationLine`` fields a ``v_irrigation_line_state`` row decides."""

    model_config = _FROZEN

    state: LineState
    fault: FaultKind | None = None
    fault_until: AwareDatetime | None = None
    vpd_source: VpdSource | None = None


def relay_cause(line_id: str, on_edge: datetime | None, bridge_run_at: datetime | None = None) -> IrrigationCause:
    """Why a relay ran when no ledger row says: ``run_now`` for an L01 ON edge within 30 s after a sent bridge
    command (its ``sent_at``), else M4's topology cause."""
    if line_id == "L01" and on_edge is not None and bridge_run_at is not None:
        if timedelta(0) <= on_edge - bridge_run_at <= timedelta(seconds=BRIDGE_RUN_WINDOW_S):
            return IrrigationCause.RUN_NOW
    return TOPOLOGY_CAUSE[line_id]


def _epoch_time(epoch: int | None) -> datetime | None:
    return None if epoch is None else datetime.fromtimestamp(epoch, UTC)


def irr_state_reading(text: object) -> IrrState | None:
    """``parse_irr_state``, with no row or text outside the grammar treated as no reading."""
    if not isinstance(text, str):
        return None
    try:
        return parse_irr_state(text)
    except ValueError:
        return None


def line_state(
    row: Any,
    *,
    engine: bool,
    reporting: bool,
    now: datetime,
    bridge_run_at: datetime | None = None,
) -> LineStateFields:
    """One ``v_irrigation_line_state`` row (a mapping or an asyncpg record) as a line's state fields. Pure.

    ``reporting`` is the architecture §7 test, computed by the caller. Not reporting, or no relay row and no
    ``irr_state``, reads ``unknown`` with ``observed_at`` of the newest row, so the age shows. ``irr_state`` is
    published on change, so its age alone never makes a line unknown.

    With the engine, this line's run token gives ``running`` with its cause, slot and planned end; otherwise a
    stored ``enabled`` false reads ``disabled``, a future ``paused_until`` ``paused``, a ``w=`` token ``waiting``
    or ``blocked`` (``WAITING_REASONS``) with its reason in words and, when known, until when in ``ends_at``, and
    anything else ``idle``. The ``f=`` and ``v=`` tokens give ``fault``, ``fault_until`` and ``vpd_source``.

    Before the engine, for L03 and L11 always, and with the engine while ``irr_state`` has no reading: relay state
    only. ON is ``running`` with ``relay_cause()`` (``relay_at`` is taken as the ON edge, so a caller that knows the
    edge passes it there), OFF is ``idle``.
    """
    data = dict(row)
    line_id = data["line_id"]
    relay_at, irr_at = data.get("relay_at"), data.get("irr_state_at")
    newest = max((at for at in (relay_at, irr_at) if at is not None), default=None)
    unknown = LineStateFields(state=LineState(code="unknown", observed_at=newest))
    if not reporting or newest is None:
        return unknown
    reading = irr_state_reading(data.get("irr_state")) if engine and line_id not in RELAY_ONLY_LINES else None
    if reading is None:
        if relay_at is None or data.get("relay_on") is None:
            return unknown
        if data["relay_on"]:
            cause = relay_cause(line_id, relay_at, bridge_run_at)
            return LineStateFields(state=LineState(code="running", cause=cause, observed_at=relay_at))
        return LineStateFields(state=LineState(code="idle", observed_at=relay_at))

    run = next((token for token in reading.runs if token.line_id == line_id), None)
    wait = next((token for token in reading.waiting if token.line_id == line_id), None)
    paused_until = data.get("paused_until")
    if run is not None:
        state = LineState(
            code="running",
            cause=run.cause,
            slot_id=run.slot_id,
            ends_at=_epoch_time(run.end_epoch),
            observed_at=irr_at,
        )
    elif not data.get("enabled"):
        state = LineState(code="disabled", reason=REASON_WORDS["dis"], observed_at=irr_at)
    elif paused_until is not None and paused_until > now:
        state = LineState(code="paused", reason=REASON_WORDS["pau"], observed_at=irr_at)
    elif wait is not None:
        state = LineState(
            code="waiting" if wait.reason in WAITING_REASONS else "blocked",
            reason=REASON_WORDS[wait.reason],
            ends_at=_epoch_time(wait.until_epoch),
            observed_at=irr_at,
        )
    else:
        state = LineState(code="idle", observed_at=irr_at)
    fault = next((token for token in reading.faults if token.line_id == line_id), None)
    sources = reading.vpd_source
    zone = _VPD_ZONE_OF_LINE.get(line_id)
    return LineStateFields(
        state=state,
        fault=None if fault is None else fault.fault,
        fault_until=None if fault is None else _epoch_time(fault.until_epoch),
        vpd_source=None if sources is None or zone is None else getattr(sources, zone),
    )
