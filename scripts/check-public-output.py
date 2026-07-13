#!/usr/bin/env python3
"""Fail closed when a public source or build tree violates publication policy."""

from __future__ import annotations

import argparse
import base64
import binascii
import codecs
import ctypes
import ctypes.util
import hashlib
import json
import os
import re
import stat
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from verdify_public.atomic_directory import (  # noqa: E402
    discard_open_directory,
    promote_open_directory,
    tree_inventory,
)
from verdify_public.output_policy import (  # noqa: E402
    PUBLIC_CROP_EXCLUDE_SLUGS,
    PUBLIC_CROP_REFERENCE_RE,
    decode_public_text,
    public_text_requires_decoding,
    redact_non_public_crop_references,
)

TEXT_SUFFIXES = frozenset(
    {
        ".css",
        ".conf",
        ".csv",
        ".html",
        ".js",
        ".json",
        ".map",
        ".md",
        ".mjs",
        ".rss",
        ".svg",
        ".scss",
        ".txt",
        ".ts",
        ".tsx",
        ".webmanifest",
        ".xml",
        ".yaml",
        ".yml",
    }
)
TEXT_FILENAMES = frozenset({".prettierrc"})
SUPPORTED_BINARY_SUFFIXES = frozenset(
    {".gz", ".gzip", ".jpeg", ".jpg", ".m4v", ".mp4", ".pdf", ".png", ".woff", ".woff2"}
)
TEXT_CHUNK_SIZE = 1024 * 1024
STREAM_OVERLAP = 64 * 1024
BINARY_CHUNK_SIZE = 1024 * 1024
MPEG_TS_PACKET_SIZE = 188
MPEG_TS_PROBE_PACKETS = 5
MPEG_TS_TEXT_PROBE_BYTES = 64 * 1024
MPEG_TS_READ_PACKETS = 4096
MPEG_TS_METADATA_MAX_BYTES = 1024 * 1024
MPEG_TS_MAX_PSI_SECTION_BYTES = 1024
MPEG_TS_METADATA_STREAM_TYPES = frozenset({0x05, 0x06, 0x0D, 0x15, 0x86})
MPEG_TS_PACKET_LAYOUTS = ((188, 0), (192, 4), (204, 0))
ISO_BMFF_MAX_BOXES = 4096
ISO_BMFF_MAX_METADATA_BYTES = 4 * 1024 * 1024
ISO_BMFF_MAX_SAMPLES = 2_000_000
ISO_BMFF_MAX_GAP_PADDING_BYTES = 16
ISO_BMFF_MAX_TOTAL_GAP_PADDING_BYTES = 4096
ISO_BMFF_MEDIA_BOXES = frozenset({b"mdat"})
ISO_BMFF_VIDEO_SAMPLE_ENTRIES = frozenset({b"av01", b"avc1", b"avc3", b"hev1", b"hvc1", b"vp08", b"vp09"})
ISO_BMFF_AUDIO_SAMPLE_ENTRIES = frozenset({b".mp3", b"Opus", b"ac-3", b"ec-3", b"fLaC", b"mp4a"})
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8"
PDF_SIGNATURE = b"%PDF-"
GZIP_SIGNATURE = b"\x1f\x8b"
WOFF_SIGNATURE = b"wOFF"
WOFF2_SIGNATURE = b"wOF2"
PNG_MAX_CHUNKS = 4096
JPEG_MAX_SEGMENTS = 4096
PDF_MAX_STREAMS = 4096
PDF_MAX_DICTIONARY_BYTES = 1024 * 1024
PDF_MAX_DICTIONARY_TOKENS = 65536
PDF_MAX_OBJECT_DEPTH = 64
PDF_MAX_LEXICAL_TOKENS = 1_000_000
PDF_MAX_FILTERS = 8
PDF_MAX_FILTER_MEMBERS = 1
PDF_MAX_FILTER_DECODE_RATIO = 256
PDF_MAX_FILTER_TOTAL_DECODED_BYTES = 128 * 1024 * 1024
PDF_MAX_FILTER_RECURSION = 4
COMPRESSED_MAX_MEMBERS = 64
COMPRESSED_METADATA_MAX_BYTES = 1024 * 1024
DECOMPRESSED_METADATA_MAX_BYTES = 1024 * 1024
COMPRESSED_ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
DECOMPRESSED_ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
GZIP_MAX_HEADER_METADATA_BYTES = 1024 * 1024
DATA_URI_MAX_ENCODED_CHARS = 64 * 1024
PNG_TEXTUAL_CHUNKS = frozenset({b"eXIf", b"iCCP", b"iTXt", b"sPLT", b"tEXt", b"zTXt"})
PNG_SAFE_BINARY_ANCILLARY_CHUNKS = frozenset(
    {
        b"acTL",
        b"bKGD",
        b"cHRM",
        b"fcTL",
        b"fdAT",
        b"gAMA",
        b"hIST",
        b"iDOT",
        b"pHYs",
        b"sBIT",
        b"sRGB",
        b"tIME",
        b"tRNS",
    }
)
PNG_CRITICAL_CHUNKS = frozenset({b"IDAT", b"IEND", b"IHDR", b"PLTE"})
UNSUPPORTED_COMPRESSED_SUFFIXES = frozenset({".7z", ".br", ".bz2", ".rar", ".tgz", ".xz", ".zip", ".zst", ".zstd"})
UNSUPPORTED_COMPRESSED_MAGICS = (
    b"PK\x03\x04",
    b"BZh",
    b"\xfd7zXZ\x00",
    b"\x28\xb5\x2f\xfd",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
)
FONT_MAX_TABLES = 4096
FONT_MAX_NAME_RECORDS = 65535
FONT_MAX_TABLE_BYTES = 64 * 1024 * 1024
FONT_MAX_DECOMPRESSED_BYTES = 128 * 1024 * 1024
WOFF2_KNOWN_TAGS = (
    b"cmap",
    b"head",
    b"hhea",
    b"hmtx",
    b"maxp",
    b"name",
    b"OS/2",
    b"post",
    b"cvt ",
    b"fpgm",
    b"glyf",
    b"loca",
    b"prep",
    b"CFF ",
    b"VORG",
    b"EBDT",
    b"EBLC",
    b"gasp",
    b"hdmx",
    b"kern",
    b"LTSH",
    b"PCLT",
    b"VDMX",
    b"vhea",
    b"vmtx",
    b"BASE",
    b"GDEF",
    b"GPOS",
    b"GSUB",
    b"EBSC",
    b"JSTF",
    b"MATH",
    b"CBDT",
    b"CBLC",
    b"COLR",
    b"CPAL",
    b"SVG ",
    b"sbix",
    b"acnt",
    b"avar",
    b"bdat",
    b"bloc",
    b"bsln",
    b"cvar",
    b"fdsc",
    b"feat",
    b"fmtx",
    b"fvar",
    b"gvar",
    b"hsty",
    b"just",
    b"lcar",
    b"mort",
    b"morx",
    b"opbd",
    b"prop",
    b"trak",
    b"Zapf",
    b"Silf",
    b"Glat",
    b"Gloc",
    b"Feat",
    b"Sill",
)
# Only tables whose payload can be scanned directly are accepted. Tables with
# embedded images/XML, signatures, arbitrary metadata, or nested compression
# remain unsupported until they have their own bounded parser.
FONT_SUPPORTED_TABLE_TAGS = frozenset(
    {
        b"BASE",
        b"CFF ",
        b"CFF2",
        b"COLR",
        b"CPAL",
        b"GDEF",
        b"GPOS",
        b"GSUB",
        b"HVAR",
        b"JSTF",
        b"LTSH",
        b"MATH",
        b"MVAR",
        b"OS/2",
        b"PCLT",
        b"STAT",
        b"VDMX",
        b"VORG",
        b"VVAR",
        b"avar",
        b"cmap",
        b"cvar",
        b"cvt ",
        b"fpgm",
        b"fvar",
        b"gasp",
        b"glyf",
        b"gvar",
        b"hdmx",
        b"head",
        b"hhea",
        b"hmtx",
        b"kern",
        b"loca",
        b"maxp",
        b"name",
        b"post",
        b"prep",
        b"vhea",
        b"vmtx",
    }
)
DATA_IMAGE_URI_RE = re.compile(
    rf"data:image/(?P<kind>png|jpeg|jpg);base64,(?P<payload>[A-Za-z0-9+/]{{1,{DATA_URI_MAX_ENCODED_CHARS}}}={{0,2}})"
    rf"(?![A-Za-z0-9+/=])",
    flags=re.IGNORECASE,
)
OVERSIZED_DATA_IMAGE_URI_RE = re.compile(
    rf"data:image/(?:png|jpeg|jpg);base64,[A-Za-z0-9+/]{{{DATA_URI_MAX_ENCODED_CHARS + 1}}}",
    flags=re.IGNORECASE,
)
NONFINITE_NUMBER = r"[+-]?(?:nan|inf(?:inity)?|\.(?:nan|inf))"
INVALID_NUMBER = rf"(?:none|{NONFINITE_NUMBER})"
INVALID_NUMBER_VALUE = rf"['\"]?{INVALID_NUMBER}['\"]?"
NONFINITE_NUMBER_VALUE = rf"['\"]?{NONFINITE_NUMBER}['\"]?"
INVALID_NUMBER_TOKEN_RE = re.compile(
    rf"(?<![A-Za-z0-9_]){INVALID_NUMBER}(?![A-Za-z0-9_])",
    flags=re.IGNORECASE,
)
NUMERIC_FIELD_NAME = (
    r"(?:value|score|ratio|percent(?:age)?|pct|count|total|average|avg|mean|min|max|limit|bound|"
    r"temperature|temp|humidity|vpd|dli|energy|water|power|watts?|cost|price|revenue|currency|amount|opacity)"
    r"(?:[_ .-][A-Za-z0-9]+){0,3}"
)
INVALID_RENDERED_VALUE_RE = re.compile(
    rf"(?:"
    rf"(?<![A-Za-z0-9_])(?:USD|US\$|\$)\s*[:=]?\s*{INVALID_NUMBER_VALUE}(?![A-Za-z0-9_-])"
    rf"|(?<![A-Za-z0-9_]){INVALID_NUMBER_VALUE}\s*(?:USD|US\$)(?![A-Za-z0-9_-])"
    rf")",
    flags=re.IGNORECASE,
)
INVALID_CURRENCY_FIELD_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?:cost|price|revenue|currency|amount)(?:[_ -][A-Za-z0-9]+){{0,3}}"
    rf"\s*['\"]?\s*[:=]\s*{INVALID_NUMBER_VALUE}(?![A-Za-z0-9_-])",
    flags=re.IGNORECASE,
)
INVALID_STRUCTURED_VALUE_RE = re.compile(
    rf"(?<![A-Za-z0-9_])"
    rf"(?:[A-Za-z_][A-Za-z0-9_. -]{{0,80}}|['\"][^'\"\r\n]{{1,80}}['\"])"
    rf"\s*['\"]?\s*[:=]\s*{NONFINITE_NUMBER}"
    rf"(?=\s*(?:[,}}\]\r\n#;>]|$))",
    flags=re.IGNORECASE | re.MULTILINE,
)
INVALID_QUOTED_NUMERIC_FIELD_RE = re.compile(
    rf"(?<![A-Za-z0-9_]){NUMERIC_FIELD_NAME}\s*['\"]?\s*[:=]\s*"
    rf"['\"]{NONFINITE_NUMBER}['\"](?=\s*(?:[,}}\]\r\n#;>]|$))",
    flags=re.IGNORECASE | re.MULTILINE,
)
INVALID_NULL_NUMERIC_FIELD_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?!max-(?:width|height|inline-size|block-size)\s*:\s*none)"
    rf"{NUMERIC_FIELD_NAME}\s*['\"]?\s*[:=]\s*['\"]?none['\"]?"
    rf"(?=\s*(?:[,}}\]\r\n#;>]|$))",
    flags=re.IGNORECASE | re.MULTILINE,
)
INVALID_MEASURED_VALUE_RE = re.compile(
    rf"(?<![A-Za-z0-9_]){INVALID_NUMBER_VALUE}\s*"
    rf"(?:%|°[CF]|kPa|Pa|ppm|k?W(?:h)?|m?L|gal(?:lons?)?|kg|lb|oz)"
    rf"(?![A-Za-z0-9_])",
    flags=re.IGNORECASE,
)
INVALID_DELIMITED_VALUE_RE = re.compile(
    rf"(?P<prefix>^|[,:=|>\[({{;])\s*(?:-\s+)?{NONFINITE_NUMBER}"
    rf"(?=\s*(?:$|[,|<}}\]);#]))",
    flags=re.IGNORECASE | re.MULTILINE,
)
INVALID_VALUE_PATTERNS = (
    INVALID_RENDERED_VALUE_RE,
    INVALID_CURRENCY_FIELD_RE,
    INVALID_STRUCTURED_VALUE_RE,
    INVALID_QUOTED_NUMERIC_FIELD_RE,
    INVALID_NULL_NUMERIC_FIELD_RE,
    INVALID_MEASURED_VALUE_RE,
    INVALID_DELIMITED_VALUE_RE,
)
INVALID_VALUE_RE = re.compile(
    "(?:" + ")|(?:".join(pattern.pattern for pattern in INVALID_VALUE_PATTERNS) + ")",
    flags=re.IGNORECASE | re.MULTILINE,
)
VALUE_SCAN_TRIGGER_RE = re.compile(r"[A-Za-z0-9%&\\+$]")
INVALID_LITERAL_TOKENS = ("nan", "inf", "none", "usd", "us$", "$")


@dataclass(frozen=True, order=True)
class Finding:
    root: str
    root_identity: str
    path: str
    route: str
    reason: str


def _value_reasons(value: object) -> set[str]:
    text = str(value or "")
    if not VALUE_SCAN_TRIGGER_RE.search(text):
        return set()
    reasons: set[str] = set()
    if not public_text_requires_decoding(text):
        lowered = text.casefold()
        if any(identifier in lowered for identifier in PUBLIC_CROP_EXCLUDE_SLUGS) and PUBLIC_CROP_REFERENCE_RE.search(
            text
        ):
            reasons.add("content")
        if any(token in lowered for token in INVALID_LITERAL_TOKENS) and INVALID_VALUE_RE.search(text):
            reasons.add("invalid-rendered-value")
        return reasons
    decoded = decode_public_text(text)
    if decoded.limit_hit:
        reasons.add("decode-limit")
    for variant in decoded.variants:
        if PUBLIC_CROP_REFERENCE_RE.search(variant):
            reasons.add("content")
        if INVALID_VALUE_RE.search(variant):
            reasons.add("invalid-rendered-value")
    return reasons


def _safe_report_text(value: object) -> str:
    """Decode then sanitize paths so reports and stderr cannot echo violations."""
    decoded = decode_public_text(value)
    safe = redact_non_public_crop_references(decoded.variants[-1] if decoded.variants else "")
    has_invalid_value = any(INVALID_VALUE_RE.search(variant) for variant in decoded.variants)
    safe = INVALID_VALUE_RE.sub("invalid-rendered-value", safe)
    if has_invalid_value or decoded.limit_hit:
        safe = INVALID_NUMBER_TOKEN_RE.sub("invalid-rendered-value", safe)
    if decoded.limit_hit:
        safe = "bounded-value"
    return "".join(char if char >= " " and char != "\x7f" else "?" for char in safe)


def public_route(relative_path: Path) -> str:
    """Map source Markdown or built HTML to its stable public route."""
    suffix = relative_path.suffix.casefold()
    if suffix in {".md", ".html"}:
        without_suffix = relative_path.with_suffix("")
        parts = list(without_suffix.parts)
        if parts and parts[-1] == "index":
            parts.pop()
        return "/" + "/".join(parts) if parts else "/"
    return "/" + relative_path.as_posix()


def _looks_utf16(data: bytes) -> str | None:
    sample = data[: min(len(data), 4096)]
    if sample.startswith(codecs.BOM_UTF16_LE):
        return "utf-16-le"
    if sample.startswith(codecs.BOM_UTF16_BE):
        return "utf-16-be"
    if len(sample) < 8:
        return None
    even = sample[0::2]
    odd = sample[1::2]
    even_zero = even.count(0) / max(1, len(even))
    odd_zero = odd.count(0) / max(1, len(odd))
    if odd_zero >= 0.4 and even_zero <= 0.1:
        return "utf-16-le"
    if even_zero >= 0.4 and odd_zero <= 0.1:
        return "utf-16-be"
    return None


def _scan_data_image_uris(text: str, *, final: bool = True) -> set[str]:
    reasons: set[str] = set()
    if "data:image/" not in text.casefold():
        return reasons
    if OVERSIZED_DATA_IMAGE_URI_RE.search(text):
        reasons.add("decode-limit")
    for match in DATA_IMAGE_URI_RE.finditer(text):
        if not final and match.end() == len(text):
            continue
        try:
            payload = base64.b64decode(match.group("payload"), validate=True)
        except (binascii.Error, ValueError):
            reasons.add("malformed-compressed-artifact")
            continue
        kind = match.group("kind").casefold()
        if kind == "png":
            reasons.update(_scan_png_bytes(payload))
        else:
            reasons.update(_scan_jpeg_bytes(payload))
    return reasons


def _scan_text_stream(file_descriptor: int) -> set[str]:
    reasons: set[str] = set()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    carry = ""
    offset = 0
    try:
        while chunk := os.pread(file_descriptor, TEXT_CHUNK_SIZE, offset):
            decoded = decoder.decode(chunk)
            if "\x00" in decoded:
                reasons.update(_scan_binary_stream(file_descriptor))
                return reasons
            combined = carry + decoded
            scan_text = combined
            if (
                offset
                and scan_text
                and scan_text[0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_+/=-"
            ):
                boundary = re.search(r"[^A-Za-z0-9_+/=-]", scan_text)
                if boundary is not None:
                    scan_text = scan_text[boundary.end() :]
            reasons.update(_value_reasons(scan_text))
            reasons.update(_scan_data_image_uris(scan_text, final=False))
            carry = combined[-STREAM_OVERLAP:]
            offset += len(chunk)
        tail = decoder.decode(b"", final=True)
        if tail:
            combined = carry + tail
            reasons.update(_value_reasons(combined))
        if "data:image/" in carry.casefold():
            reasons.update(_scan_data_image_uris(carry, final=True))
    except UnicodeDecodeError:
        reasons.add("unreadable-text")
        reasons.update(_scan_binary_stream(file_descriptor))
    return reasons


def _scan_binary_stream(file_descriptor: int) -> set[str]:
    """Scan raw metadata once per chunk, adding one UTF-16 pass only when indicated."""
    return _scan_binary_range(file_descriptor, 0, os.fstat(file_descriptor).st_size)


def _scan_binary_range(file_descriptor: int, start: int, length: int) -> set[str]:
    """Scan one bounded byte range without interpreting adjacent container data."""
    reasons: set[str] = set()
    carry = b""
    offset = start
    end = start + length
    while offset < end:
        chunk = os.pread(file_descriptor, min(BINARY_CHUNK_SIZE, end - offset), offset)
        if not chunk:
            reasons.add("malformed-media-artifact")
            break
        combined = carry + chunk
        reasons.update(_value_reasons(combined.decode("utf-8", errors="ignore")))
        encoding = _looks_utf16(combined)
        if encoding is not None:
            payload = combined[: len(combined) - (len(combined) % 2)]
            reasons.update(_value_reasons(payload.decode(encoding, errors="ignore")))
        carry = combined[-STREAM_OVERLAP:]
        offset += len(chunk)
    return reasons


def _is_utf8_text_probe(payload: bytes) -> bool:
    """Keep ordinary TypeScript on the text path without trusting its shared suffix."""
    if not payload:
        return True
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\x00" not in payload


def _mpeg_ts_probe_layouts(payload: bytes) -> tuple[tuple[int, int], ...]:
    """Return packet layouts with a deterministic run of transport sync bytes."""
    layouts: list[tuple[int, int]] = []
    for packet_size, sync_offset in MPEG_TS_PACKET_LAYOUTS:
        available_packets = 0 if len(payload) <= sync_offset else 1 + (len(payload) - 1 - sync_offset) // packet_size
        probe_packets = min(MPEG_TS_PROBE_PACKETS, available_packets)
        if probe_packets < 3:
            continue
        if all(payload[sync_offset + packet_size * index] == 0x47 for index in range(probe_packets)):
            layouts.append((packet_size, sync_offset))
    return tuple(layouts)


def _mpeg_ts_crc32(payload: bytes) -> int:
    """Return the ISO/IEC 13818-1 CRC remainder used by PAT and PMT sections."""
    value = 0xFFFFFFFF
    for byte in payload:
        value ^= byte << 24
        for _ in range(8):
            if value & 0x80000000:
                value = ((value << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                value = (value << 1) & 0xFFFFFFFF
    return value


def _mpeg_ts_parse_pat(section: bytes) -> tuple[tuple[int, int] | None, bool]:
    """Parse one single-program HLS PAT, returning (program, PMT PID)."""
    if (
        len(section) < 12
        or section[0] != 0x00
        or section[1] & 0xF0 != 0xB0
        or 3 + (((section[1] & 0x0F) << 8) | section[2]) != len(section)
        or section[5] & 0xC1 != 0xC1
        or section[6] != 0
        or section[7] != 0
        or _mpeg_ts_crc32(section) != 0
    ):
        return None, True
    program_bytes = section[8:-4]
    if len(program_bytes) % 4:
        return None, True
    programs: list[tuple[int, int]] = []
    seen_programs: set[int] = set()
    for offset in range(0, len(program_bytes), 4):
        program = (program_bytes[offset] << 8) | program_bytes[offset + 1]
        if program_bytes[offset + 2] & 0xE0 != 0xE0 or program in seen_programs:
            return None, True
        seen_programs.add(program)
        pid = ((program_bytes[offset + 2] & 0x1F) << 8) | program_bytes[offset + 3]
        if program:
            programs.append((program, pid))
    if len(programs) != 1 or programs[0][1] in {0, 0x1FFF}:
        return None, True
    return programs[0], False


def _mpeg_ts_parse_pmt(
    section: bytes,
    expected_program: int,
) -> tuple[tuple[int, tuple[tuple[int, int], ...]] | None, bool]:
    """Parse one HLS PMT, returning its PCR PID and elementary stream map."""
    if (
        len(section) < 16
        or section[0] != 0x02
        or section[1] & 0xF0 != 0xB0
        or 3 + (((section[1] & 0x0F) << 8) | section[2]) != len(section)
        or ((section[3] << 8) | section[4]) != expected_program
        or section[5] & 0xC1 != 0xC1
        or section[6] != 0
        or section[7] != 0
        or section[8] & 0xE0 != 0xE0
        or section[10] & 0xF0 != 0xF0
        or _mpeg_ts_crc32(section) != 0
    ):
        return None, True
    pcr_pid = ((section[8] & 0x1F) << 8) | section[9]
    program_info_length = ((section[10] & 0x0F) << 8) | section[11]
    offset = 12 + program_info_length
    payload_end = len(section) - 4
    if offset > payload_end:
        return None, True
    streams: list[tuple[int, int]] = []
    seen_pids: set[int] = set()
    while offset < payload_end:
        if offset + 5 > payload_end or section[offset + 1] & 0xE0 != 0xE0 or section[offset + 3] & 0xF0 != 0xF0:
            return None, True
        stream_type = section[offset]
        elementary_pid = ((section[offset + 1] & 0x1F) << 8) | section[offset + 2]
        descriptor_length = ((section[offset + 3] & 0x0F) << 8) | section[offset + 4]
        offset += 5
        if (
            offset + descriptor_length > payload_end
            or elementary_pid in seen_pids
            or elementary_pid <= 0x1F
            or elementary_pid == 0x1FFF
        ):
            return None, True
        seen_pids.add(elementary_pid)
        streams.append((stream_type, elementary_pid))
        offset += descriptor_length
    if not streams:
        return None, True
    if pcr_pid != 0x1FFF and pcr_pid not in seen_pids:
        return None, True
    return (pcr_pid, tuple(streams)), False


def _mpeg_ts_append_section_data(
    buffer: bytearray,
    payload: bytes,
) -> tuple[list[bytes], bool]:
    """Append bytes at a PSI section boundary and retain a bounded tail."""
    sections: list[bytes] = []
    cursor = 0
    while cursor < len(payload):
        if not buffer and payload[cursor] == 0xFF:
            return sections, any(byte != 0xFF for byte in payload[cursor:])
        if len(buffer) < 3:
            take = min(3 - len(buffer), len(payload) - cursor)
            buffer.extend(payload[cursor : cursor + take])
            cursor += take
            if len(buffer) < 3:
                return sections, False
        section_size = 3 + (((buffer[1] & 0x0F) << 8) | buffer[2])
        if section_size < 4 or section_size > MPEG_TS_MAX_PSI_SECTION_BYTES:
            return sections, True
        take = min(section_size - len(buffer), len(payload) - cursor)
        buffer.extend(payload[cursor : cursor + take])
        cursor += take
        if len(buffer) < section_size:
            return sections, False
        sections.append(bytes(buffer))
        buffer.clear()
    return sections, False


def _mpeg_ts_feed_psi(
    buffer: bytearray,
    payload: bytes,
    payload_unit_start: bool,
) -> tuple[list[bytes], bool]:
    """Reassemble bounded PSI sections while validating pointer-field framing."""
    if not payload_unit_start:
        if not buffer:
            return [], False
        sections, malformed = _mpeg_ts_append_section_data(buffer, payload)
        if malformed or len(sections) > 1:
            return sections, True
        return sections, False
    if not payload:
        return [], True
    pointer = payload[0]
    if pointer > len(payload) - 1:
        return [], True
    prefix = payload[1 : 1 + pointer]
    sections: list[bytes] = []
    if buffer:
        completed, malformed = _mpeg_ts_append_section_data(buffer, prefix)
        if malformed or buffer or len(completed) != 1:
            return completed, True
        sections.extend(completed)
    elif any(byte != 0xFF for byte in prefix):
        return [], True
    new_sections, malformed = _mpeg_ts_append_section_data(buffer, payload[1 + pointer :])
    sections.extend(new_sections)
    return sections, malformed


def _mpeg_ts_adaptation_field(packet: memoryview, control: int) -> tuple[int, bytes, bool]:
    """Validate an adaptation field and return payload offset plus private data."""
    if control not in {2, 3}:
        return 4, b"", False
    length = packet[4]
    if length > 183 or (control == 2 and length != 183) or (control == 3 and length > 182):
        return 0, b"", True
    end = 5 + length
    if length == 0:
        return end, b"", False
    flags = packet[5]
    cursor = 6
    if flags & 0x10:
        cursor += 6
    if flags & 0x08:
        cursor += 6
    if flags & 0x04:
        cursor += 1
    private_data = b""
    if flags & 0x02:
        if cursor >= end:
            return 0, b"", True
        private_length = packet[cursor]
        cursor += 1
        if cursor + private_length > end:
            return 0, b"", True
        private_data = bytes(packet[cursor : cursor + private_length])
        cursor += private_length
    if flags & 0x01:
        if cursor >= end:
            return 0, b"", True
        extension_length = packet[cursor]
        cursor += 1 + extension_length
    if cursor > end or any(byte != 0xFF for byte in packet[cursor:end]):
        return 0, b"", True
    return end, private_data, False


def _scan_mpeg_ts_stream(file_descriptor: int) -> set[str]:
    """Validate every 188-byte packet and scan only bounded transport metadata."""
    metadata: dict[int, bytearray] = {}
    metadata_size = 0
    metadata_limit_hit = False
    pat_buffer = bytearray()
    pmt_buffer = bytearray()
    program_map: tuple[int, int] | None = None
    pmt: tuple[int, tuple[tuple[int, int], ...]] | None = None
    elementary_pids: set[int] = set()
    metadata_pids: set[int] = set()
    file_size = os.fstat(file_descriptor).st_size
    if file_size < MPEG_TS_PACKET_SIZE * 3 or file_size % MPEG_TS_PACKET_SIZE:
        return {"malformed-media-artifact"}

    def retain_metadata(pid: int, payload: bytes) -> None:
        nonlocal metadata_limit_hit, metadata_size
        if not payload or metadata_limit_hit:
            return
        if metadata_size + len(payload) > MPEG_TS_METADATA_MAX_BYTES:
            metadata_limit_hit = True
            return
        metadata.setdefault(pid, bytearray()).extend(payload)
        metadata_size += len(payload)

    offset = 0
    read_size = MPEG_TS_PACKET_SIZE * MPEG_TS_READ_PACKETS
    while offset < file_size:
        chunk = os.pread(file_descriptor, min(read_size, file_size - offset), offset)
        if not chunk or len(chunk) % MPEG_TS_PACKET_SIZE:
            return {"malformed-media-artifact"}
        view = memoryview(chunk)
        for packet_offset in range(0, len(chunk), MPEG_TS_PACKET_SIZE):
            packet = view[packet_offset : packet_offset + MPEG_TS_PACKET_SIZE]
            if packet[0] != 0x47 or packet[1] & 0x80 or packet[3] & 0xC0:
                return {"malformed-media-artifact"}
            pid = ((packet[1] & 0x1F) << 8) | packet[2]
            payload_unit_start = bool(packet[1] & 0x40)
            control = (packet[3] >> 4) & 0x03
            if control == 0:
                return {"malformed-media-artifact"}
            payload_start, private_data, malformed = _mpeg_ts_adaptation_field(packet, control)
            if malformed or (control == 2 and payload_unit_start):
                return {"malformed-media-artifact"}
            retain_metadata(-1, private_data)
            if control not in {1, 3}:
                continue
            payload = bytes(packet[payload_start:])
            # Null packets are padding, not an unclassified metadata channel.
            # Verdify HLS emits canonical 0xff payload stuffing, so any start
            # indicator or non-padding byte is malformed and fails closed.
            if pid == 0x1FFF:
                if payload_unit_start or any(byte != 0xFF for byte in payload):
                    return {"malformed-media-artifact"}
                continue
            # A PID cannot be classified as compressed A/V versus public
            # metadata until its PMT has been validated. Accepting elementary
            # payload before that point silently discarded it from the bounded
            # metadata scan. Verdify HLS is single-program PAT/PMT-first, so
            # fail closed on premature or undeclared elementary payload.
            if pid > 0x1F:
                if pmt is None:
                    if program_map is None or pid != program_map[1]:
                        return {"malformed-media-artifact"}
                else:
                    if pid not in elementary_pids and pid != program_map[1]:
                        return {"malformed-media-artifact"}
            if pid <= 0x1F or (program_map is not None and pid == program_map[1]) or pid in metadata_pids:
                retain_metadata(pid, payload)
            if pid == 0:
                sections, malformed = _mpeg_ts_feed_psi(pat_buffer, payload, payload_unit_start)
                if malformed:
                    return {"malformed-media-artifact"}
                for section in sections:
                    parsed, malformed = _mpeg_ts_parse_pat(section)
                    if malformed or (program_map is not None and parsed != program_map):
                        return {"malformed-media-artifact"}
                    program_map = parsed
            elif program_map is not None and pid == program_map[1]:
                sections, malformed = _mpeg_ts_feed_psi(pmt_buffer, payload, payload_unit_start)
                if malformed:
                    return {"malformed-media-artifact"}
                for section in sections:
                    parsed, malformed = _mpeg_ts_parse_pmt(section, program_map[0])
                    if malformed or (pmt is not None and parsed != pmt):
                        return {"malformed-media-artifact"}
                    pmt = parsed
                    elementary_pids.update(elementary_pid for _stream_type, elementary_pid in parsed[1])
                    metadata_pids.update(
                        elementary_pid
                        for stream_type, elementary_pid in parsed[1]
                        if stream_type in MPEG_TS_METADATA_STREAM_TYPES
                    )
        offset += len(chunk)
    if program_map is None or pmt is None or pat_buffer or pmt_buffer:
        return {"malformed-media-artifact"}
    reasons = {"media-metadata-limit"} if metadata_limit_hit else set()
    for payload in metadata.values():
        reasons.update(_scan_metadata_bytes(bytes(payload)))
    return reasons


def _scan_typescript_or_mpeg_ts(file_descriptor: int) -> set[str]:
    """Disambiguate the shared .ts suffix, rejecting opaque or ambiguous media."""
    probe = os.pread(file_descriptor, MPEG_TS_TEXT_PROBE_BYTES, 0)
    text_candidate = _is_utf8_text_probe(probe) or _looks_utf16(probe) is not None
    layouts = _mpeg_ts_probe_layouts(probe)
    if not layouts:
        return _scan_text_stream(file_descriptor) if text_candidate else {"malformed-media-artifact"}
    if text_candidate or len(layouts) != 1:
        return {"ambiguous-media-artifact"}
    if layouts[0] != (MPEG_TS_PACKET_SIZE, 0):
        return {"unsupported-media-packet-size"}
    return _scan_mpeg_ts_stream(file_descriptor)


def _iso_bmff_child_boxes(payload: bytes) -> tuple[list[tuple[bytes, bytes]], bool]:
    """Return one complete level of ISO-BMFF boxes from a bounded payload."""
    boxes: list[tuple[bytes, bytes]] = []
    offset = 0
    while offset < len(payload):
        if len(payload) - offset < 8 or len(boxes) >= ISO_BMFF_MAX_BOXES:
            return [], True
        size32, box_type = struct.unpack(">I4s", payload[offset : offset + 8])
        header_size = 8
        if size32 == 1:
            if len(payload) - offset < 16:
                return [], True
            box_size = struct.unpack(">Q", payload[offset + 8 : offset + 16])[0]
            header_size = 16
        elif size32 == 0:
            box_size = len(payload) - offset
        else:
            box_size = size32
        if box_size < header_size or offset + box_size > len(payload):
            return [], True
        boxes.append((box_type, payload[offset + header_size : offset + box_size]))
        offset += box_size
    return boxes, offset != len(payload)


def _iso_bmff_one_box(boxes: list[tuple[bytes, bytes]], box_type: bytes) -> bytes | None:
    matches = [payload for candidate, payload in boxes if candidate == box_type]
    return matches[0] if len(matches) == 1 else None


def _iso_bmff_uint_table(payload: bytes, width: int) -> tuple[list[int], bool]:
    if len(payload) < 8 or payload[:4] != b"\x00\x00\x00\x00":
        return [], True
    count = struct.unpack(">I", payload[4:8])[0]
    if count > ISO_BMFF_MAX_SAMPLES or len(payload) != 8 + count * width:
        return [], True
    return [int.from_bytes(payload[offset : offset + width], "big") for offset in range(8, len(payload), width)], False


def _iso_bmff_sample_sizes(stsz: bytes | None, stz2: bytes | None) -> tuple[list[int], bool]:
    if (stsz is None) == (stz2 is None):
        return [], True
    if stsz is not None:
        if len(stsz) < 12 or stsz[:4] != b"\x00\x00\x00\x00":
            return [], True
        sample_size, count = struct.unpack(">II", stsz[4:12])
        if count > ISO_BMFF_MAX_SAMPLES:
            return [], True
        if sample_size:
            return ([sample_size] * count, len(stsz) != 12)
        if len(stsz) != 12 + count * 4:
            return [], True
        return [struct.unpack(">I", stsz[offset : offset + 4])[0] for offset in range(12, len(stsz), 4)], False

    assert stz2 is not None
    if len(stz2) < 12 or stz2[:7] != b"\x00" * 7:
        return [], True
    field_size = stz2[7]
    count = struct.unpack(">I", stz2[8:12])[0]
    if count > ISO_BMFF_MAX_SAMPLES or field_size not in {4, 8, 16}:
        return [], True
    encoded_size = (count * field_size + 7) // 8
    if len(stz2) != 12 + encoded_size:
        return [], True
    encoded = stz2[12:]
    if field_size == 4:
        if count % 2 and encoded[-1] & 0x0F:
            return [], True
        return [
            encoded[index // 2] >> 4 if index % 2 == 0 else encoded[index // 2] & 0x0F for index in range(count)
        ], False
    width = field_size // 8
    return [int.from_bytes(encoded[offset : offset + width], "big") for offset in range(0, len(encoded), width)], False


def _iso_bmff_track_ranges(moov: bytes) -> tuple[list[tuple[int, int]], bool, bool]:
    """Prove byte ranges occupied by whitelisted audio/video samples."""
    moov_boxes, malformed = _iso_bmff_child_boxes(moov)
    if malformed:
        return [], True, True
    tracks = [payload for box_type, payload in moov_boxes if box_type == b"trak"]
    if not tracks:
        return [], True, False

    safe_ranges: list[tuple[int, int]] = []
    unproven = False
    for track in tracks:
        track_boxes, malformed = _iso_bmff_child_boxes(track)
        mdia = _iso_bmff_one_box(track_boxes, b"mdia")
        if malformed or mdia is None:
            return [], True, True
        mdia_boxes, malformed = _iso_bmff_child_boxes(mdia)
        handler = _iso_bmff_one_box(mdia_boxes, b"hdlr")
        minf = _iso_bmff_one_box(mdia_boxes, b"minf")
        if malformed or handler is None or minf is None or len(handler) < 12 or handler[:4] != b"\x00\x00\x00\x00":
            return [], True, True
        handler_type = handler[8:12]
        minf_boxes, malformed = _iso_bmff_child_boxes(minf)
        stbl = _iso_bmff_one_box(minf_boxes, b"stbl")
        if malformed or stbl is None:
            return [], True, True
        sample_boxes, malformed = _iso_bmff_child_boxes(stbl)
        if malformed:
            return [], True, True

        stsd = _iso_bmff_one_box(sample_boxes, b"stsd")
        stsc = _iso_bmff_one_box(sample_boxes, b"stsc")
        stsz = _iso_bmff_one_box(sample_boxes, b"stsz")
        stz2 = _iso_bmff_one_box(sample_boxes, b"stz2")
        stco = _iso_bmff_one_box(sample_boxes, b"stco")
        co64 = _iso_bmff_one_box(sample_boxes, b"co64")
        if stsd is None or stsc is None or (stco is None) == (co64 is None):
            return [], True, True

        if len(stsd) < 8 or stsd[:4] != b"\x00\x00\x00\x00":
            return [], True, True
        sample_entries, malformed = _iso_bmff_child_boxes(stsd[8:])
        entry_count = struct.unpack(">I", stsd[4:8])[0]
        if malformed or entry_count != len(sample_entries) or not sample_entries:
            return [], True, True

        if len(stsc) < 8 or stsc[:4] != b"\x00\x00\x00\x00":
            return [], True, True
        mapping_count = struct.unpack(">I", stsc[4:8])[0]
        if mapping_count > ISO_BMFF_MAX_SAMPLES or len(stsc) != 8 + mapping_count * 12 or not mapping_count:
            return [], True, True
        mappings = [struct.unpack(">III", stsc[offset : offset + 12]) for offset in range(8, len(stsc), 12)]
        if (
            mappings[0][0] != 1
            or any(
                not samples_per_chunk or not description_index
                for _first, samples_per_chunk, description_index in mappings
            )
            or any(current[0] <= previous[0] for previous, current in zip(mappings, mappings[1:], strict=False))
        ):
            return [], True, True
        if any(description_index > entry_count for _first, _count, description_index in mappings):
            return [], True, True

        sizes, malformed = _iso_bmff_sample_sizes(stsz, stz2)
        offsets, offset_malformed = _iso_bmff_uint_table(
            stco if stco is not None else co64 or b"", 4 if stco is not None else 8
        )
        if malformed or offset_malformed:
            return [], True, True

        sample_index = 0
        mapping_index = 0
        ranges: list[tuple[int, int]] = []
        used_descriptions: set[int] = set()
        for chunk_index, chunk_offset in enumerate(offsets, start=1):
            while mapping_index + 1 < len(mappings) and mappings[mapping_index + 1][0] <= chunk_index:
                mapping_index += 1
            _first_chunk, samples_per_chunk, description_index = mappings[mapping_index]
            if sample_index + samples_per_chunk > len(sizes):
                return [], True, True
            chunk_size = sum(sizes[sample_index : sample_index + samples_per_chunk])
            sample_index += samples_per_chunk
            used_descriptions.add(description_index)
            if chunk_size:
                ranges.append((chunk_offset, chunk_offset + chunk_size))
        if sample_index != len(sizes):
            return [], True, True

        allowed_entries = (
            ISO_BMFF_VIDEO_SAMPLE_ENTRIES
            if handler_type == b"vide"
            else ISO_BMFF_AUDIO_SAMPLE_ENTRIES
            if handler_type == b"soun"
            else frozenset()
        )
        if not ranges or any(sample_entries[index - 1][0] not in allowed_entries for index in used_descriptions):
            unproven = True
            continue
        safe_ranges.extend(ranges)
    return safe_ranges, unproven, False


def _iso_bmff_gap_is_padding(file_descriptor: int, start: int, length: int) -> bool:
    """Allow only a tiny all-zero alignment gap outside proven A/V samples."""
    if length <= 0 or length > ISO_BMFF_MAX_GAP_PADDING_BYTES:
        return False
    payload = os.pread(file_descriptor, length, start)
    return len(payload) == length and not any(payload)


def _scan_iso_bmff_stream(file_descriptor: int) -> set[str]:
    """Validate top-level ISO-BMFF boxes and scan metadata plus text-bearing media."""
    file_size = os.fstat(file_descriptor).st_size
    if file_size < 16:
        return {"malformed-media-artifact"}
    reasons: set[str] = set()
    offset = 0
    boxes = 0
    metadata_bytes = 0
    first_type: bytes | None = None
    seen_file_type = False
    seen_moov = False
    moov_payload: bytes | None = None
    media_ranges: list[tuple[int, int]] = []
    while offset < file_size:
        header = os.pread(file_descriptor, 8, offset)
        if len(header) != 8:
            return {"malformed-media-artifact"}
        size32, box_type = struct.unpack(">I4s", header)
        header_size = 8
        if size32 == 1:
            extended = os.pread(file_descriptor, 8, offset + 8)
            if len(extended) != 8:
                return {"malformed-media-artifact"}
            box_size = struct.unpack(">Q", extended)[0]
            header_size = 16
        elif size32 == 0:
            box_size = file_size - offset
        else:
            box_size = size32
        if box_size < header_size or offset + box_size > file_size:
            return {"malformed-media-artifact"}
        boxes += 1
        if boxes > ISO_BMFF_MAX_BOXES:
            return {"media-metadata-limit"}
        payload_size = box_size - header_size
        if first_type is None:
            first_type = box_type
        if box_type == b"ftyp":
            if seen_file_type or payload_size < 8 or (payload_size - 8) % 4:
                return {"malformed-media-artifact"}
            seen_file_type = True
        if box_type == b"moov":
            if seen_moov:
                return {"malformed-media-artifact"}
            seen_moov = True
        if box_type in ISO_BMFF_MEDIA_BOXES:
            media_ranges.append((offset + header_size, offset + box_size))
        else:
            metadata_bytes += payload_size
            if metadata_bytes > ISO_BMFF_MAX_METADATA_BYTES:
                return {"media-metadata-limit"}
            payload = os.pread(file_descriptor, payload_size, offset + header_size)
            if len(payload) != payload_size:
                return {"malformed-media-artifact"}
            reasons.update(_scan_metadata_bytes(payload))
            if box_type == b"moov":
                moov_payload = payload
        offset += box_size
        if size32 == 0 and offset != file_size:
            return {"malformed-media-artifact"}
    if offset != file_size or first_type != b"ftyp" or not seen_file_type or not seen_moov:
        return {"malformed-media-artifact", *reasons}
    if not media_ranges:
        return reasons
    if moov_payload is None:
        return {"malformed-media-artifact", *reasons}

    safe_ranges, unproven, malformed = _iso_bmff_track_ranges(moov_payload)
    safe_ranges.sort()
    if malformed or any(start >= end for start, end in safe_ranges):
        return {"malformed-media-artifact", *reasons}
    if any(current[0] < previous[1] for previous, current in zip(safe_ranges, safe_ranges[1:], strict=False)):
        return {"malformed-media-artifact", *reasons}
    if any(
        not any(media_start <= start and end <= media_end for media_start, media_end in media_ranges)
        for start, end in safe_ranges
    ):
        return {"malformed-media-artifact", *reasons}

    padding_bytes = 0
    for media_start, media_end in media_ranges:
        cursor = media_start
        for sample_start, sample_end in safe_ranges:
            if sample_end <= media_start:
                continue
            if sample_start >= media_end:
                break
            if sample_start > cursor:
                gap_size = sample_start - cursor
                padding_bytes += gap_size
                if padding_bytes > ISO_BMFF_MAX_TOTAL_GAP_PADDING_BYTES or not _iso_bmff_gap_is_padding(
                    file_descriptor, cursor, gap_size
                ):
                    return {"malformed-media-artifact", *reasons}
            cursor = sample_end
        if cursor < media_end:
            gap_size = media_end - cursor
            padding_bytes += gap_size
            if padding_bytes > ISO_BMFF_MAX_TOTAL_GAP_PADDING_BYTES or not _iso_bmff_gap_is_padding(
                file_descriptor, cursor, gap_size
            ):
                return {"malformed-media-artifact", *reasons}
    if unproven:
        reasons.add("malformed-media-artifact")
    return reasons


def _bounded_decompress_members(
    payload: bytes,
    *,
    wbits: int,
    compressed_limit: int,
    decompressed_limit: int,
    limit_reason: str,
    malformed_reason: str,
    member_limit: int = COMPRESSED_MAX_MEMBERS,
) -> tuple[bytes | None, str | None]:
    if len(payload) > compressed_limit:
        return None, limit_reason
    remaining = payload
    decoded_parts: list[bytes] = []
    decoded_size = 0
    members = 0
    while remaining:
        members += 1
        if members > member_limit:
            return None, limit_reason
        try:
            decompressor = zlib.decompressobj(wbits)
            decoded = decompressor.decompress(remaining, decompressed_limit - decoded_size + 1)
            if decompressor.unconsumed_tail or decoded_size + len(decoded) > decompressed_limit:
                return None, limit_reason
            decoded += decompressor.flush(decompressed_limit - decoded_size - len(decoded) + 1)
        except (ValueError, zlib.error):
            return None, malformed_reason
        if decoded_size + len(decoded) > decompressed_limit:
            return None, limit_reason
        if not decompressor.eof:
            return None, malformed_reason
        decoded_parts.append(decoded)
        decoded_size += len(decoded)
        unused = decompressor.unused_data
        consumed = len(remaining) - len(unused)
        if consumed <= 0:
            return None, malformed_reason
        remaining = unused
    return b"".join(decoded_parts), None


def _bounded_zlib_decompress(payload: bytes) -> tuple[bytes | None, str | None]:
    return _bounded_decompress_members(
        payload,
        wbits=zlib.MAX_WBITS,
        compressed_limit=COMPRESSED_METADATA_MAX_BYTES,
        decompressed_limit=DECOMPRESSED_METADATA_MAX_BYTES,
        limit_reason="compressed-metadata-limit",
        malformed_reason="malformed-compressed-metadata",
    )


def _scan_decoded_bytes(payload: bytes, encoding: str, malformed_reason: str) -> set[str]:
    try:
        return _value_reasons(payload.decode(encoding))
    except UnicodeDecodeError:
        return {malformed_reason}


def _scan_metadata_bytes(payload: bytes) -> set[str]:
    """Scan bounded binary metadata without interpreting compressed pixels."""
    if len(payload) > DECOMPRESSED_METADATA_MAX_BYTES:
        return {"compressed-metadata-limit"}
    reasons = _value_reasons(payload.decode("latin-1", errors="ignore"))
    encoding = _looks_utf16(payload)
    if encoding is not None:
        even_payload = payload[: len(payload) - len(payload) % 2]
        reasons.update(_value_reasons(even_payload.decode(encoding, errors="ignore")))
    return reasons


def _valid_png_keyword(keyword: bytes) -> bool:
    return bool(keyword) and len(keyword) <= 79 and b"\x00" not in keyword


def _scan_png_bytes(data: bytes) -> set[str]:
    if not data.startswith(PNG_SIGNATURE):
        return {"malformed-compressed-artifact"}
    reasons: set[str] = set()
    offset = len(PNG_SIGNATURE)
    chunk_count = 0
    saw_iend = False
    while offset + 12 <= len(data):
        chunk_count += 1
        if chunk_count > PNG_MAX_CHUNKS:
            reasons.add("compressed-metadata-limit")
            break
        length, chunk_type = struct.unpack(">I4s", data[offset : offset + 8])
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            reasons.add("malformed-compressed-metadata")
            break
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
            reasons.add("malformed-compressed-metadata")
            break
        if chunk_type in PNG_TEXTUAL_CHUNKS and length > COMPRESSED_METADATA_MAX_BYTES:
            reasons.add("compressed-metadata-limit")
        elif chunk_type == b"tEXt":
            keyword, separator, text = payload.partition(b"\x00")
            if not separator or not _valid_png_keyword(keyword):
                reasons.add("malformed-compressed-metadata")
            else:
                reasons.update(_scan_decoded_bytes(keyword + b" " + text, "latin-1", "malformed-compressed-metadata"))
        elif chunk_type == b"zTXt":
            keyword, separator, remainder = payload.partition(b"\x00")
            if not separator or not _valid_png_keyword(keyword) or len(remainder) < 2 or remainder[0] != 0:
                reasons.add("malformed-compressed-metadata")
            else:
                decoded, error = _bounded_zlib_decompress(remainder[1:])
                if error:
                    reasons.add(error)
                elif decoded is not None:
                    reasons.update(
                        _scan_decoded_bytes(keyword + b" " + decoded, "latin-1", "malformed-compressed-metadata")
                    )
        elif chunk_type == b"iTXt":
            keyword, separator, remainder = payload.partition(b"\x00")
            if not separator or not _valid_png_keyword(keyword) or len(remainder) < 2:
                reasons.add("malformed-compressed-metadata")
            else:
                compressed_flag, compression_method = remainder[0], remainder[1]
                language, language_separator, remainder = remainder[2:].partition(b"\x00")
                translated, translated_separator, text = remainder.partition(b"\x00")
                if not language_separator or not translated_separator or compression_method != 0:
                    reasons.add("malformed-compressed-metadata")
                elif compressed_flag == 0:
                    reasons.update(
                        _scan_decoded_bytes(
                            keyword + b" " + language + b" " + translated + b" " + text,
                            "utf-8",
                            "malformed-compressed-metadata",
                        )
                    )
                elif compressed_flag == 1:
                    decoded, error = _bounded_zlib_decompress(text)
                    if error:
                        reasons.add(error)
                    elif decoded is not None:
                        reasons.update(
                            _scan_decoded_bytes(
                                keyword + b" " + language + b" " + translated + b" " + decoded,
                                "utf-8",
                                "malformed-compressed-metadata",
                            )
                        )
                else:
                    reasons.add("malformed-compressed-metadata")
        elif chunk_type == b"iCCP":
            profile_name, separator, remainder = payload.partition(b"\x00")
            if not separator or not _valid_png_keyword(profile_name) or len(remainder) < 2 or remainder[0] != 0:
                reasons.add("malformed-compressed-metadata")
            else:
                reasons.update(_scan_decoded_bytes(profile_name, "latin-1", "malformed-compressed-metadata"))
                decoded, error = _bounded_zlib_decompress(remainder[1:])
                if error:
                    reasons.add(error)
                elif decoded is not None:
                    reasons.update(_scan_metadata_bytes(decoded))
        elif chunk_type == b"eXIf":
            reasons.update(_scan_metadata_bytes(payload))
        elif chunk_type == b"sPLT":
            palette_name, separator, remainder = payload.partition(b"\x00")
            if not separator or not _valid_png_keyword(palette_name) or not remainder or remainder[0] not in {8, 16}:
                reasons.add("malformed-compressed-metadata")
            else:
                entry_size = 6 if remainder[0] == 8 else 10
                if not remainder[1:] or len(remainder[1:]) % entry_size:
                    reasons.add("malformed-compressed-metadata")
                reasons.update(_scan_decoded_bytes(palette_name, "latin-1", "malformed-compressed-metadata"))
        elif chunk_type not in PNG_CRITICAL_CHUNKS and chunk_type not in PNG_SAFE_BINARY_ANCILLARY_CHUNKS:
            # Unknown ancillary chunks may carry textual application metadata.
            # Without a bounded parser their content is not eligible to publish.
            reasons.add("unsupported-compressed-container")
        offset = chunk_end
        if chunk_type == b"IEND":
            saw_iend = True
            if length != 0 or offset != len(data):
                reasons.add("malformed-compressed-metadata")
            break
    if not saw_iend:
        reasons.add("malformed-compressed-metadata")
    return reasons


def _jpeg_marker_kind(marker: int, *, in_entropy: bool) -> str:
    """Classify one post-FF marker identically on both sides of a scan."""
    if marker in {0x00, 0xFF, 0xD8} or 0x02 <= marker <= 0xBF:
        return "invalid"
    if 0xD0 <= marker <= 0xD7:
        return "restart" if in_entropy else "invalid"
    if marker == 0xD9:
        return "eoi"
    if marker == 0x01:
        return "standalone"
    return "segment"


def _scan_jpeg_bytes(data: bytes) -> set[str]:
    if not data.startswith(JPEG_SIGNATURE):
        return {"malformed-compressed-artifact"}
    reasons: set[str] = set()
    offset = 2
    markers = 0
    in_scan = False
    while True:
        if in_scan:
            while offset < len(data):
                if data[offset] != 0xFF:
                    offset += 1
                    continue
                marker_start = offset
                offset += 1
                while offset < len(data) and data[offset] == 0xFF:
                    offset += 1
                if offset >= len(data):
                    return {"malformed-compressed-artifact", *reasons}
                marker = data[offset]
                offset += 1
                if marker == 0x00:
                    # Byte stuffing is exactly FF 00; fill bytes may precede
                    # real markers but cannot extend a stuffed data byte.
                    if offset - marker_start != 2:
                        return {"malformed-compressed-artifact", *reasons}
                    continue
                marker_kind = _jpeg_marker_kind(marker, in_entropy=True)
                if marker_kind == "invalid":
                    return {"malformed-compressed-artifact", *reasons}
                markers += 1
                if markers > JPEG_MAX_SEGMENTS:
                    return {"compressed-artifact-limit", *reasons}
                if marker_kind == "restart":
                    continue
                in_scan = False
                break
            else:
                return {"malformed-compressed-artifact", *reasons}
        else:
            if offset >= len(data) or data[offset] != 0xFF:
                return {"malformed-compressed-artifact", *reasons}
            offset += 1
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                return {"malformed-compressed-artifact", *reasons}
            marker = data[offset]
            offset += 1
            marker_kind = _jpeg_marker_kind(marker, in_entropy=False)
            if marker_kind == "invalid":
                return {"malformed-compressed-artifact", *reasons}
            markers += 1
            if markers > JPEG_MAX_SEGMENTS:
                return {"compressed-artifact-limit", *reasons}

        if marker_kind == "eoi":
            return reasons if offset == len(data) else {"malformed-compressed-artifact", *reasons}
        if marker_kind == "standalone":
            continue
        if offset + 2 > len(data):
            return {"malformed-compressed-artifact", *reasons}
        length = struct.unpack(">H", data[offset : offset + 2])[0]
        if length < 2 or offset + length > len(data):
            return {"malformed-compressed-artifact", *reasons}
        segment = data[offset + 2 : offset + length]
        if marker == 0xFE or 0xE0 <= marker <= 0xEF:
            text = segment.decode("utf-8", errors="ignore")
            reasons.update(_value_reasons(text))
            encoding = _looks_utf16(segment)
            if encoding:
                reasons.update(
                    _value_reasons(segment[: len(segment) - len(segment) % 2].decode(encoding, errors="ignore"))
                )
        offset += length
        if marker == 0xDA:
            in_scan = True


def _pdf_dictionary_end(data: bytes, start: int, stream_marker: int) -> tuple[int | None, str | None]:
    depth = 0
    literal_depth = 0
    offset = start
    while offset < stream_marker:
        if offset - start > PDF_MAX_DICTIONARY_BYTES:
            return None, "compressed-artifact-limit"
        byte = data[offset]
        if literal_depth:
            if byte == 0x5C:
                offset += 2
                continue
            if byte == 0x28:
                literal_depth += 1
            elif byte == 0x29:
                literal_depth -= 1
            offset += 1
            continue
        if byte == 0x25:
            offset += 1
            while offset < stream_marker and data[offset] not in {0x0A, 0x0D}:
                offset += 1
            continue
        if byte == 0x28:
            literal_depth = 1
            offset += 1
            continue
        if data[offset : offset + 2] == b"<<":
            depth += 1
            offset += 2
            continue
        if data[offset : offset + 2] == b">>":
            depth -= 1
            offset += 2
            if depth == 0:
                return offset, None
            if depth < 0:
                return None, "malformed-compressed-artifact"
            continue
        if byte == 0x3C:
            end = data.find(b">", offset + 1, stream_marker)
            if end < 0:
                return None, "malformed-compressed-artifact"
            offset = end + 1
            continue
        offset += 1
    return None, "malformed-compressed-artifact"


def _pdf_gap_is_trivia(data: bytes) -> bool:
    offset = 0
    while offset < len(data):
        if data[offset] in b"\x00\t\n\x0c\r ":
            offset += 1
            continue
        if data[offset] == 0x25:
            offset += 1
            while offset < len(data) and data[offset] not in {0x0A, 0x0D}:
                offset += 1
            if offset == len(data):
                return False
            continue
        return False
    return True


def _find_pdf_stream_dictionary(
    data: bytes,
    stream_marker: int,
    candidates: list[int],
) -> tuple[bytes | None, str | None]:
    window_start = max(0, stream_marker - PDF_MAX_DICTIONARY_BYTES)
    saw_limit = False
    for candidate in reversed([value for value in candidates if value >= window_start]):
        end, error = _pdf_dictionary_end(data, candidate, stream_marker)
        if error == "compressed-artifact-limit":
            saw_limit = True
            continue
        if end is not None:
            gap = data[end:stream_marker]
            if _pdf_gap_is_trivia(gap):
                return data[candidate:end], None
    if saw_limit or any(value < window_start for value in candidates):
        return None, "compressed-artifact-limit"
    return None, "malformed-compressed-artifact"


@dataclass(frozen=True)
class _PDFToken:
    kind: str
    value: bytes = b""


@dataclass(frozen=True)
class _PDFObject:
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class _PDFDictionary:
    tokens: tuple[_PDFToken, ...]
    entries: dict[bytes, _PDFObject]


_PDF_WHITESPACE = frozenset(b"\x00\t\n\x0c\r ")
_PDF_DELIMITERS = frozenset(b"()<>[]{}/%")
_PDF_HEX_DIGITS = frozenset(b"0123456789abcdefABCDEF")


def _pdf_regular_token_end(data: bytes, offset: int) -> int:
    while offset < len(data) and data[offset] not in _PDF_WHITESPACE and data[offset] not in _PDF_DELIMITERS:
        offset += 1
    return offset


def _pdf_name_token(data: bytes, offset: int) -> tuple[_PDFToken | None, int, str | None]:
    end = _pdf_regular_token_end(data, offset + 1)
    raw = data[offset + 1 : end]
    decoded = bytearray()
    index = 0
    while index < len(raw):
        if raw[index] != 0x23:
            decoded.append(raw[index])
            index += 1
            continue
        if index + 2 >= len(raw) or raw[index + 1] not in _PDF_HEX_DIGITS or raw[index + 2] not in _PDF_HEX_DIGITS:
            return None, end, "malformed-compressed-artifact"
        decoded.append(int(raw[index + 1 : index + 3], 16))
        index += 3
    return _PDFToken("name", bytes(decoded)), end, None


def _pdf_literal_string_end(data: bytes, offset: int) -> tuple[int | None, str | None]:
    depth = 1
    offset += 1
    while offset < len(data):
        byte = data[offset]
        if byte == 0x5C:
            offset += 1
            if offset >= len(data):
                return None, "malformed-compressed-artifact"
            if data[offset] == 0x0D and offset + 1 < len(data) and data[offset + 1] == 0x0A:
                offset += 2
            else:
                offset += 1
            continue
        if byte == 0x28:
            depth += 1
        elif byte == 0x29:
            depth -= 1
            if depth == 0:
                return offset + 1, None
        offset += 1
    return None, "malformed-compressed-artifact"


def _pdf_number_kind(value: bytes) -> str | None:
    if re.fullmatch(rb"[+-]?[0-9]+", value):
        return "integer"
    if re.fullmatch(rb"[+-]?(?:[0-9]+\.[0-9]*|\.[0-9]+)", value):
        return "real"
    return None


def _pdf_dictionary_tokens(dictionary: bytes) -> tuple[list[_PDFToken] | None, str | None]:
    if len(dictionary) > PDF_MAX_DICTIONARY_BYTES:
        return None, "compressed-artifact-limit"
    tokens: list[_PDFToken] = []
    offset = 0

    def append(token: _PDFToken) -> str | None:
        tokens.append(token)
        if len(tokens) > PDF_MAX_DICTIONARY_TOKENS:
            return "compressed-artifact-limit"
        return None

    while offset < len(dictionary):
        byte = dictionary[offset]
        if byte in _PDF_WHITESPACE:
            offset += 1
            continue
        if byte == 0x25:
            offset += 1
            while offset < len(dictionary) and dictionary[offset] not in {0x0A, 0x0D}:
                offset += 1
            continue
        if dictionary[offset : offset + 2] == b"<<":
            error = append(_PDFToken("dictionary-start"))
            offset += 2
        elif dictionary[offset : offset + 2] == b">>":
            error = append(_PDFToken("dictionary-end"))
            offset += 2
        elif byte == 0x5B:
            error = append(_PDFToken("array-start"))
            offset += 1
        elif byte == 0x5D:
            error = append(_PDFToken("array-end"))
            offset += 1
        elif byte == 0x2F:
            token, offset, error = _pdf_name_token(dictionary, offset)
            if error:
                return None, error
            assert token is not None
            error = append(token)
        elif byte == 0x28:
            offset, error = _pdf_literal_string_end(dictionary, offset)
            if error:
                return None, error
            assert offset is not None
            error = append(_PDFToken("literal-string"))
        elif byte == 0x3C:
            end = dictionary.find(b">", offset + 1)
            if end < 0:
                return None, "malformed-compressed-artifact"
            if any(
                value not in _PDF_WHITESPACE and value not in _PDF_HEX_DIGITS for value in dictionary[offset + 1 : end]
            ):
                return None, "malformed-compressed-artifact"
            error = append(_PDFToken("hex-string"))
            offset = end + 1
        elif byte in {0x29, 0x3E, 0x7B, 0x7D}:
            return None, "malformed-compressed-artifact"
        else:
            end = _pdf_regular_token_end(dictionary, offset)
            if end == offset:
                return None, "malformed-compressed-artifact"
            value = dictionary[offset:end]
            error = append(_PDFToken(_pdf_number_kind(value) or "keyword", value))
            offset = end
        if error:
            return None, error
    return tokens, None


def _pdf_nonnegative_integer(value: bytes, maximum: int) -> tuple[int | None, str | None]:
    if value.startswith(b"-"):
        return None, "unsupported-compressed-container"
    digits = value[1:] if value.startswith(b"+") else value
    result = 0
    for digit in digits:
        result = result * 10 + digit - 0x30
        if result > maximum:
            return None, "compressed-artifact-limit"
    return result, None


def _pdf_parse_object(tokens: list[_PDFToken], offset: int, depth: int = 0) -> tuple[int, str, str | None]:
    if offset >= len(tokens):
        return offset, "", "malformed-compressed-artifact"
    if depth > PDF_MAX_OBJECT_DEPTH:
        return offset, "", "compressed-artifact-limit"
    token = tokens[offset]
    if token.kind == "integer":
        if (
            offset + 2 < len(tokens)
            and tokens[offset + 1].kind == "integer"
            and tokens[offset + 2] == _PDFToken("keyword", b"R")
            and not token.value.startswith(b"-")
            and not tokens[offset + 1].value.startswith(b"-")
        ):
            return offset + 3, "indirect-reference", None
        return offset + 1, "integer", None
    if token.kind in {"real", "name", "literal-string", "hex-string"}:
        return offset + 1, token.kind, None
    if token.kind == "keyword":
        if token.value not in {b"true", b"false", b"null"}:
            return offset, "", "malformed-compressed-artifact"
        return offset + 1, "keyword", None
    if token.kind == "array-start":
        offset += 1
        while offset < len(tokens) and tokens[offset].kind != "array-end":
            offset, _, error = _pdf_parse_object(tokens, offset, depth + 1)
            if error:
                return offset, "", error
        if offset >= len(tokens):
            return offset, "", "malformed-compressed-artifact"
        return offset + 1, "array", None
    if token.kind == "dictionary-start":
        offset += 1
        while offset < len(tokens) and tokens[offset].kind != "dictionary-end":
            if tokens[offset].kind != "name":
                return offset, "", "malformed-compressed-artifact"
            offset, _, error = _pdf_parse_object(tokens, offset + 1, depth + 1)
            if error:
                return offset, "", error
        if offset >= len(tokens):
            return offset, "", "malformed-compressed-artifact"
        return offset + 1, "dictionary", None
    return offset, "", "malformed-compressed-artifact"


def _pdf_top_level_dictionary(dictionary: bytes) -> tuple[_PDFDictionary | None, str | None]:
    tokens, error = _pdf_dictionary_tokens(dictionary)
    if error:
        return None, error
    assert tokens is not None
    if not tokens or tokens[0].kind != "dictionary-start":
        return None, "malformed-compressed-artifact"
    offset = 1
    entries: dict[bytes, _PDFObject] = {}
    while offset < len(tokens) and tokens[offset].kind != "dictionary-end":
        key = tokens[offset]
        if key.kind != "name":
            return None, "malformed-compressed-artifact"
        if key.value in entries:
            reason = "unsupported-compressed-container" if key.value == b"Length" else "malformed-compressed-artifact"
            return None, reason
        value_offset = offset + 1
        offset, value_kind, error = _pdf_parse_object(tokens, value_offset, 1)
        if error:
            return None, error
        entries[key.value] = _PDFObject(value_kind, value_offset, offset)
    if offset != len(tokens) - 1 or tokens[offset].kind != "dictionary-end":
        return None, "malformed-compressed-artifact"
    return _PDFDictionary(tuple(tokens), entries), None


def _pdf_parsed_stream_length(dictionary: _PDFDictionary) -> tuple[int | None, str | None]:
    value = dictionary.entries.get(b"Length")
    if value is None:
        return None, "malformed-compressed-artifact"
    if value.kind != "integer":
        return None, "unsupported-compressed-container"
    length, error = _pdf_nonnegative_integer(
        dictionary.tokens[value.start].value,
        DECOMPRESSED_ARTIFACT_MAX_BYTES,
    )
    if error:
        return None, error
    return length, None


def _pdf_direct_stream_length(dictionary: bytes) -> tuple[int | None, str | None]:
    parsed, error = _pdf_top_level_dictionary(dictionary)
    if error:
        return None, error
    assert parsed is not None
    return _pdf_parsed_stream_length(parsed)


def _pdf_stream_filters(dictionary: _PDFDictionary) -> tuple[list[bytes] | None, str | None]:
    unsupported_filter_keys = {b"DP", b"DecodeParms", b"F", b"FDecodeParms", b"FFilter"}
    if unsupported_filter_keys.intersection(dictionary.entries):
        # PNG/TIFF predictors and per-filter parameter arrays change the decoded
        # byte representation. None are publishable until explicitly bounded and
        # implemented here.
        return None, "unsupported-compressed-container"
    value = dictionary.entries.get(b"Filter")
    if value is None:
        return [], None
    if value.kind == "name":
        return [dictionary.tokens[value.start].value], None
    if value.kind != "array":
        return None, "malformed-compressed-artifact"
    filters: list[bytes] = []
    for token in dictionary.tokens[value.start + 1 : value.end - 1]:
        if token.kind != "name":
            return None, "malformed-compressed-artifact"
        filters.append(token.value)
    if not filters:
        return None, "malformed-compressed-artifact"
    if len(filters) > PDF_MAX_FILTERS:
        return None, "compressed-artifact-limit"
    return filters, None


def _pdf_stream_is_image(dictionary: _PDFDictionary) -> bool:
    value = dictionary.entries.get(b"Subtype")
    return value is not None and value.kind == "name" and dictionary.tokens[value.start].value == b"Image"


def _decode_pdf_filter_chain(
    stream: bytes,
    filters: list[bytes],
    *,
    is_image: bool,
) -> tuple[bytes | None, str | None, str | None]:
    """Apply supported PDF filters in declaration order under per-stage bounds."""
    if len(filters) > PDF_MAX_FILTERS:
        return None, None, "compressed-artifact-limit"
    aliases = {
        b"FlateDecode": "flate",
        b"Fl": "flate",
        b"DCTDecode": "jpeg",
        b"DCT": "jpeg",
    }
    payload = stream
    total_decoded = 0
    terminal_format: str | None = None
    for index, filter_name in enumerate(filters):
        filter_kind = aliases.get(filter_name)
        if filter_kind is None:
            return None, None, "unsupported-compressed-container"
        if filter_kind == "jpeg":
            # DCT decoding produces pixels, which this guard deliberately does
            # not implement. Preserve and scan the encoded JPEG metadata, so DCT
            # must be the terminal image filter in the chain.
            if not is_image or index != len(filters) - 1:
                return None, None, "unsupported-compressed-container"
            terminal_format = "jpeg"
            continue

        decoded, error = _bounded_decompress_members(
            payload,
            wbits=zlib.MAX_WBITS,
            compressed_limit=COMPRESSED_ARTIFACT_MAX_BYTES,
            decompressed_limit=DECOMPRESSED_ARTIFACT_MAX_BYTES,
            limit_reason="compressed-artifact-limit",
            malformed_reason="malformed-compressed-artifact",
            member_limit=PDF_MAX_FILTER_MEMBERS,
        )
        if error:
            return None, None, error
        assert decoded is not None
        if payload and len(decoded) > len(payload) * PDF_MAX_FILTER_DECODE_RATIO:
            return None, None, "compressed-artifact-limit"
        total_decoded += len(decoded)
        if total_decoded > PDF_MAX_FILTER_TOTAL_DECODED_BYTES:
            return None, None, "compressed-artifact-limit"
        payload = decoded
    return payload, terminal_format, None


def _scan_pdf_decoded_payload(
    payload: bytes,
    *,
    terminal_format: str | None,
    recursion_depth: int,
) -> set[str]:
    if terminal_format == "jpeg":
        return _scan_jpeg_bytes(payload)
    if payload.startswith(PNG_SIGNATURE):
        return _scan_png_bytes(payload)
    if payload.startswith(JPEG_SIGNATURE):
        return _scan_jpeg_bytes(payload)
    if payload.startswith(PDF_SIGNATURE):
        if recursion_depth >= PDF_MAX_FILTER_RECURSION:
            return {"compressed-artifact-limit"}
        return _scan_pdf_bytes(payload, recursion_depth=recursion_depth + 1)
    if payload.startswith(GZIP_SIGNATURE):
        if recursion_depth >= PDF_MAX_FILTER_RECURSION:
            return {"compressed-artifact-limit"}
        return _scan_gzip_bytes(payload, recursion_depth=recursion_depth + 1)
    if any(payload.startswith(magic) for magic in UNSUPPORTED_COMPRESSED_MAGICS):
        return {"unsupported-compressed-container"}
    return _value_reasons(payload.decode("latin-1", errors="ignore"))


def _pdf_next_stream_context(
    data: bytes,
    offset: int,
) -> tuple[int | None, list[int], str | None]:
    dictionary_candidates: list[int] = []
    tokens_seen = 0
    while offset < len(data):
        byte = data[offset]
        if byte in _PDF_WHITESPACE:
            offset += 1
            continue
        tokens_seen += 1
        if tokens_seen > PDF_MAX_LEXICAL_TOKENS:
            return None, dictionary_candidates, "compressed-artifact-limit"
        if byte == 0x25:
            offset += 1
            while offset < len(data) and data[offset] not in {0x0A, 0x0D}:
                offset += 1
            continue
        if byte == 0x28:
            end, error = _pdf_literal_string_end(data, offset)
            if error:
                return None, dictionary_candidates, error
            assert end is not None
            offset = end
            continue
        if data[offset : offset + 2] == b"<<":
            dictionary_candidates.append(offset)
            offset += 2
            continue
        if data[offset : offset + 2] == b">>":
            offset += 2
            continue
        if byte == 0x3C:
            end = data.find(b">", offset + 1)
            if end < 0:
                return None, dictionary_candidates, "malformed-compressed-artifact"
            if any(value not in _PDF_WHITESPACE and value not in _PDF_HEX_DIGITS for value in data[offset + 1 : end]):
                return None, dictionary_candidates, "malformed-compressed-artifact"
            offset = end + 1
            continue
        if byte == 0x2F:
            _, offset, error = _pdf_name_token(data, offset)
            if error:
                return None, dictionary_candidates, error
            continue
        if byte in _PDF_DELIMITERS:
            offset += 1
            continue
        end = _pdf_regular_token_end(data, offset)
        if end == offset:
            return None, dictionary_candidates, "malformed-compressed-artifact"
        if data[offset:end] == b"stream":
            return offset, dictionary_candidates, None
        offset = end
    return None, dictionary_candidates, None


def _pdf_keyword_at(data: bytes, offset: int, keyword: bytes) -> bool:
    end = offset + len(keyword)
    if data[offset:end] != keyword:
        return False
    before = offset == 0 or data[offset - 1] in _PDF_WHITESPACE or data[offset - 1] in _PDF_DELIMITERS
    after = end == len(data) or data[end] in _PDF_WHITESPACE or data[end] in _PDF_DELIMITERS
    return before and after


def _scan_pdf_bytes(data: bytes, *, recursion_depth: int = 0) -> set[str]:
    if recursion_depth > PDF_MAX_FILTER_RECURSION:
        return {"compressed-artifact-limit"}
    if not data.startswith(PDF_SIGNATURE) or b"%%EOF" not in data[-1024:]:
        return {"malformed-compressed-artifact"}
    reasons: set[str] = set()
    cursor = 0
    search_offset = 0
    stream_count = 0
    while True:
        stream_marker, dictionary_candidates, keyword_error = _pdf_next_stream_context(data, search_offset)
        if keyword_error:
            reasons.add(keyword_error)
            return reasons
        if stream_marker is None:
            break
        keyword_end = stream_marker + len(b"stream")
        search_offset = keyword_end
        dictionary, dictionary_error = _find_pdf_stream_dictionary(data, stream_marker, dictionary_candidates)
        if dictionary_error:
            reasons.add(dictionary_error)
            return reasons
        if dictionary is None:
            continue
        stream_count += 1
        if stream_count > PDF_MAX_STREAMS:
            reasons.add("compressed-artifact-limit")
            return reasons
        if data[keyword_end : keyword_end + 2] == b"\r\n":
            stream_start = keyword_end + 2
        elif data[keyword_end : keyword_end + 1] in {b"\r", b"\n"}:
            stream_start = keyword_end + 1
        else:
            reasons.add("malformed-compressed-artifact")
            return reasons
        parsed_dictionary, dictionary_error = _pdf_top_level_dictionary(dictionary)
        if dictionary_error:
            reasons.add(dictionary_error)
            return reasons
        assert parsed_dictionary is not None
        reasons.update(_value_reasons(data[cursor:stream_start].decode("latin-1", errors="ignore")))

        stream_length, length_error = _pdf_parsed_stream_length(parsed_dictionary)
        if length_error:
            reasons.add(length_error)
            return reasons
        assert stream_length is not None
        stream_end = stream_start + stream_length
        if stream_end > len(data):
            reasons.add("malformed-compressed-artifact")
            return reasons
        end_marker = stream_end
        if data[end_marker : end_marker + 2] == b"\r\n":
            end_marker += 2
        elif data[end_marker : end_marker + 1] in {b"\r", b"\n"}:
            end_marker += 1
        else:
            reasons.add("malformed-compressed-artifact")
            return reasons
        if not _pdf_keyword_at(data, end_marker, b"endstream"):
            reasons.add("malformed-compressed-artifact")
            return reasons

        stream = data[stream_start:stream_end]
        filters, filter_error = _pdf_stream_filters(parsed_dictionary)
        if filter_error:
            reasons.add(filter_error)
            return reasons
        is_image = _pdf_stream_is_image(parsed_dictionary)
        decoded, terminal_format, filter_error = _decode_pdf_filter_chain(stream, filters, is_image=is_image)
        if filter_error:
            reasons.add(filter_error)
            return reasons
        assert decoded is not None
        reasons.update(
            _scan_pdf_decoded_payload(
                decoded,
                terminal_format=terminal_format,
                recursion_depth=recursion_depth,
            )
        )
        cursor = end_marker + len(b"endstream")
        search_offset = cursor
    reasons.update(_value_reasons(data[cursor:].decode("latin-1", errors="ignore")))
    return reasons


def _read_bounded(file_descriptor: int, limit: int, limit_reason: str) -> tuple[bytes | None, str | None]:
    size = os.fstat(file_descriptor).st_size
    if size > limit:
        return None, limit_reason
    chunks: list[bytes] = []
    offset = 0
    while chunk := os.pread(file_descriptor, BINARY_CHUNK_SIZE, offset):
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks), None


def _gzip_cstring(
    data: bytes,
    offset: int,
    remaining_metadata: int,
) -> tuple[bytes | None, int, int, str | None]:
    search_end = min(len(data), offset + remaining_metadata + 1)
    terminator = data.find(b"\x00", offset, search_end)
    if terminator < 0:
        reason = (
            "compressed-artifact-limit" if len(data) - offset > remaining_metadata else "malformed-compressed-artifact"
        )
        return None, offset, remaining_metadata, reason
    field = data[offset:terminator]
    if len(field) > remaining_metadata:
        return None, offset, remaining_metadata, "compressed-artifact-limit"
    return field, terminator + 1, remaining_metadata - len(field), None


def _decode_gzip_members(data: bytes) -> tuple[bytes | None, set[str], str | None]:
    if len(data) > COMPRESSED_ARTIFACT_MAX_BYTES:
        return None, set(), "compressed-artifact-limit"
    reasons: set[str] = set()
    decoded_parts: list[bytes] = []
    decoded_size = 0
    metadata_remaining = GZIP_MAX_HEADER_METADATA_BYTES
    offset = 0
    members = 0
    while offset < len(data):
        members += 1
        if members > COMPRESSED_MAX_MEMBERS:
            return None, reasons, "compressed-artifact-limit"
        header_start = offset
        if offset + 10 > len(data) or data[offset : offset + 2] != GZIP_SIGNATURE or data[offset + 2] != 8:
            return None, reasons, "malformed-compressed-artifact"
        flags = data[offset + 3]
        if flags & 0xE0:
            return None, reasons, "malformed-compressed-artifact"
        offset += 10
        if flags & 0x04:
            if offset + 2 > len(data):
                return None, reasons, "malformed-compressed-artifact"
            extra_length = struct.unpack("<H", data[offset : offset + 2])[0]
            offset += 2
            if extra_length > metadata_remaining:
                return None, reasons, "compressed-artifact-limit"
            if offset + extra_length > len(data):
                return None, reasons, "malformed-compressed-artifact"
            extra = data[offset : offset + extra_length]
            reasons.update(_scan_metadata_bytes(extra))
            metadata_remaining -= extra_length
            offset += extra_length
        for flag in (0x08, 0x10):
            if not flags & flag:
                continue
            field, offset, metadata_remaining, error = _gzip_cstring(data, offset, metadata_remaining)
            if error:
                return None, reasons, error
            reasons.update(_scan_decoded_bytes(field or b"", "latin-1", "malformed-compressed-artifact"))
        if flags & 0x02:
            if offset + 2 > len(data):
                return None, reasons, "malformed-compressed-artifact"
            expected_header_crc = struct.unpack("<H", data[offset : offset + 2])[0]
            if zlib.crc32(data[header_start:offset]) & 0xFFFF != expected_header_crc:
                return None, reasons, "malformed-compressed-artifact"
            offset += 2

        remaining_output = DECOMPRESSED_ARTIFACT_MAX_BYTES - decoded_size
        try:
            decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
            decoded = decompressor.decompress(data[offset:], remaining_output + 1)
            if decompressor.unconsumed_tail or len(decoded) > remaining_output:
                return None, reasons, "compressed-artifact-limit"
            decoded += decompressor.flush(remaining_output - len(decoded) + 1)
        except (ValueError, zlib.error):
            return None, reasons, "malformed-compressed-artifact"
        if len(decoded) > remaining_output:
            return None, reasons, "compressed-artifact-limit"
        if not decompressor.eof:
            return None, reasons, "malformed-compressed-artifact"
        deflate_length = len(data) - offset - len(decompressor.unused_data)
        if deflate_length <= 0:
            return None, reasons, "malformed-compressed-artifact"
        trailer_offset = offset + deflate_length
        if trailer_offset + 8 > len(data):
            return None, reasons, "malformed-compressed-artifact"
        expected_crc, expected_size = struct.unpack("<II", data[trailer_offset : trailer_offset + 8])
        if zlib.crc32(decoded) & 0xFFFFFFFF != expected_crc or len(decoded) & 0xFFFFFFFF != expected_size:
            return None, reasons, "malformed-compressed-artifact"
        decoded_parts.append(decoded)
        decoded_size += len(decoded)
        offset = trailer_offset + 8
    return b"".join(decoded_parts), reasons, None


def _scan_gzip_bytes(data: bytes, *, recursion_depth: int = 0) -> set[str]:
    if recursion_depth > PDF_MAX_FILTER_RECURSION:
        return {"compressed-artifact-limit"}
    decoded, reasons, error = _decode_gzip_members(data)
    if error:
        reasons.add(error)
        return reasons
    if decoded is None:
        return {"malformed-compressed-artifact", *reasons}
    if decoded.startswith(GZIP_SIGNATURE):
        return {"unsupported-compressed-container", *reasons}
    if any(decoded.startswith(magic) for magic in UNSUPPORTED_COMPRESSED_MAGICS):
        return {"unsupported-compressed-container", *reasons}
    if decoded.startswith(PNG_SIGNATURE):
        reasons.update(_scan_png_bytes(decoded))
        return reasons
    if decoded.startswith(JPEG_SIGNATURE):
        reasons.update(_scan_jpeg_bytes(decoded))
        return reasons
    if decoded.startswith(PDF_SIGNATURE):
        reasons.update(_scan_pdf_bytes(decoded, recursion_depth=recursion_depth + 1))
        return reasons
    try:
        reasons.update(_value_reasons(decoded.decode("utf-8")))
    except UnicodeDecodeError:
        reasons.update(_value_reasons(decoded.decode("utf-8", errors="ignore")))
        encoding = _looks_utf16(decoded)
        if encoding:
            reasons.update(_value_reasons(decoded[: len(decoded) - len(decoded) % 2].decode(encoding, errors="ignore")))
    return reasons


def _font_xml_reasons(payload: bytes) -> set[str]:
    if len(payload) > DECOMPRESSED_METADATA_MAX_BYTES:
        return {"compressed-metadata-limit"}
    try:
        text = payload.decode("utf-8")
        # WOFF metadata cannot require a DTD. Reject declarations before using
        # the bounded stdlib parser so entity expansion is not reachable.
        if "<!DOCTYPE" in text or "<!ENTITY" in text:
            return {"malformed-compressed-metadata"}
        ET.fromstring(text)  # noqa: S314 -- bounded input with DTDs/entities rejected above
    except (ET.ParseError, UnicodeDecodeError):
        return {"malformed-compressed-metadata"}
    return _value_reasons(text)


def _scan_sfnt_name_table(payload: bytes) -> set[str]:
    if len(payload) < 6:
        return {"malformed-compressed-artifact"}
    table_format, count, string_offset = struct.unpack(">HHH", payload[:6])
    if table_format not in {0, 1} or count > FONT_MAX_NAME_RECORDS:
        return {"malformed-compressed-artifact" if count <= FONT_MAX_NAME_RECORDS else "compressed-artifact-limit"}
    records_end = 6 + count * 12
    if records_end > len(payload) or string_offset < records_end or string_offset > len(payload):
        return {"malformed-compressed-artifact"}
    language_records: list[tuple[int, int]] = []
    if table_format == 1:
        if records_end + 2 > len(payload):
            return {"malformed-compressed-artifact"}
        language_count = struct.unpack(">H", payload[records_end : records_end + 2])[0]
        if language_count > FONT_MAX_NAME_RECORDS or records_end + 2 + language_count * 4 > string_offset:
            return {
                "malformed-compressed-artifact"
                if language_count <= FONT_MAX_NAME_RECORDS
                else "compressed-artifact-limit"
            }
        for index in range(language_count):
            start = records_end + 2 + index * 4
            language_records.append(struct.unpack(">HH", payload[start : start + 4]))

    reasons: set[str] = set()
    storage = payload[string_offset:]
    reasons.update(_scan_metadata_bytes(storage))
    if len(storage) >= 2:
        even_storage = storage[: len(storage) - len(storage) % 2]
        reasons.update(_value_reasons(even_storage.decode("utf-16-be", errors="ignore")))
        reasons.update(_value_reasons(even_storage.decode("utf-16-le", errors="ignore")))
    for length, offset in language_records:
        if offset + length > len(storage):
            reasons.add("malformed-compressed-artifact")
            continue
        try:
            reasons.update(_value_reasons(storage[offset : offset + length].decode("utf-16-be")))
        except UnicodeDecodeError:
            reasons.add("malformed-compressed-artifact")
    for index in range(count):
        start = 6 + index * 12
        platform_id, encoding_id, _language_id, _name_id, length, offset = struct.unpack(
            ">HHHHHH", payload[start : start + 12]
        )
        if offset + length > len(storage):
            reasons.add("malformed-compressed-artifact")
            continue
        value = storage[offset : offset + length]
        try:
            if platform_id == 0 or (platform_id == 3 and encoding_id in {0, 1, 10}):
                decoded = value.decode("utf-16-be")
            elif platform_id == 1 and encoding_id == 0:
                decoded = value.decode("mac-roman")
            else:
                decoded = value.decode("latin-1")
        except UnicodeDecodeError:
            reasons.add("malformed-compressed-artifact")
            continue
        reasons.update(_value_reasons(decoded))
    return reasons


def _sfnt_table_checksum(tag: bytes, payload: bytes) -> int:
    checksum_payload = payload
    if tag == b"head" and len(payload) >= 12:
        checksum_payload = payload[:8] + b"\x00\x00\x00\x00" + payload[12:]
    padding = (-len(checksum_payload)) % 4
    padded = checksum_payload + b"\x00" * padding
    return sum(struct.unpack(f">{len(padded) // 4}I", padded)) & 0xFFFFFFFF if padded else 0


def _zlib_decompress_exact(payload: bytes, expected_length: int) -> tuple[bytes | None, str | None]:
    if expected_length > FONT_MAX_TABLE_BYTES:
        return None, "compressed-artifact-limit"
    try:
        decompressor = zlib.decompressobj(zlib.MAX_WBITS)
        decoded = decompressor.decompress(payload, expected_length + 1)
        if decompressor.unconsumed_tail or len(decoded) > expected_length:
            return None, "compressed-artifact-limit"
        decoded += decompressor.flush(expected_length - len(decoded) + 1)
    except (ValueError, zlib.error):
        return None, "malformed-compressed-artifact"
    if (
        len(decoded) != expected_length
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        return None, "malformed-compressed-artifact"
    return decoded, None


def _scan_woff_bytes(data: bytes) -> set[str]:
    if len(data) < 44 or not data.startswith(WOFF_SIGNATURE):
        return {"malformed-compressed-artifact"}
    try:
        (
            _signature,
            flavor,
            declared_length,
            num_tables,
            reserved,
            total_sfnt_size,
            _major_version,
            _minor_version,
            meta_offset,
            meta_length,
            meta_orig_length,
            private_offset,
            private_length,
        ) = struct.unpack(">4sIIHHIHHIIIII", data[:44])
    except struct.error:
        return {"malformed-compressed-artifact"}
    if flavor not in {0x00010000, 0x4F54544F, 0x74727565, 0x74797031}:
        return {"malformed-compressed-artifact"}
    if declared_length != len(data) or reserved != 0 or not 0 < num_tables <= FONT_MAX_TABLES:
        return {"malformed-compressed-artifact" if num_tables <= FONT_MAX_TABLES else "compressed-artifact-limit"}
    directory_end = 44 + num_tables * 20
    if directory_end > len(data) or total_sfnt_size > FONT_MAX_DECOMPRESSED_BYTES or total_sfnt_size % 4:
        return {
            "compressed-artifact-limit"
            if total_sfnt_size > FONT_MAX_DECOMPRESSED_BYTES
            else "malformed-compressed-artifact"
        }

    entries: list[tuple[bytes, int, int, int, int]] = []
    tags: list[bytes] = []
    expected_sfnt_size = 12 + num_tables * 16
    for index in range(num_tables):
        start = 44 + index * 20
        tag, offset, compressed_length, original_length, original_checksum = struct.unpack(
            ">4sIIII", data[start : start + 20]
        )
        if original_length > FONT_MAX_TABLE_BYTES:
            return {"compressed-artifact-limit"}
        if (
            tag in tags
            or any(byte < 0x20 or byte > 0x7E for byte in tag)
            or compressed_length > original_length
            or (original_length == 0) != (compressed_length == 0)
            or offset % 4
            or offset < directory_end
            or offset + compressed_length > len(data)
        ):
            return {"malformed-compressed-artifact"}
        if tag not in FONT_SUPPORTED_TABLE_TAGS:
            return {"unsupported-compressed-container"}
        tags.append(tag)
        expected_sfnt_size += (original_length + 3) & ~3
        if expected_sfnt_size > FONT_MAX_DECOMPRESSED_BYTES:
            return {"compressed-artifact-limit"}
        entries.append((tag, offset, compressed_length, original_length, original_checksum))
    if tags != sorted(tags) or b"name" not in tags or total_sfnt_size != expected_sfnt_size:
        return {"malformed-compressed-artifact"}

    reasons: set[str] = set()
    cursor = directory_end
    for tag, offset, compressed_length, original_length, original_checksum in sorted(entries, key=lambda item: item[1]):
        if offset != cursor:
            return {"malformed-compressed-artifact"}
        compressed = data[offset : offset + compressed_length]
        if compressed_length < original_length:
            decoded, error = _zlib_decompress_exact(compressed, original_length)
            if error:
                reasons.add(error)
                return reasons
            assert decoded is not None
        else:
            decoded = compressed
        if _sfnt_table_checksum(tag, decoded) != original_checksum:
            return {"malformed-compressed-artifact"}
        reasons.update(_scan_metadata_bytes(decoded))
        if tag == b"name":
            reasons.update(_scan_sfnt_name_table(decoded))
        cursor = (offset + compressed_length + 3) & ~3
        if cursor > len(data) or any(data[offset + compressed_length : cursor]):
            return {"malformed-compressed-artifact"}

    metadata_fields = (meta_offset, meta_length, meta_orig_length)
    if any(metadata_fields):
        if not all(metadata_fields) or meta_offset != cursor or meta_length > COMPRESSED_METADATA_MAX_BYTES:
            reasons.add(
                "compressed-metadata-limit"
                if meta_length > COMPRESSED_METADATA_MAX_BYTES
                else "malformed-compressed-artifact"
            )
            return reasons
        if meta_offset + meta_length > len(data) or meta_orig_length > DECOMPRESSED_METADATA_MAX_BYTES:
            reasons.add(
                "compressed-metadata-limit"
                if meta_orig_length > DECOMPRESSED_METADATA_MAX_BYTES
                else "malformed-compressed-artifact"
            )
            return reasons
        metadata, error = _zlib_decompress_exact(data[meta_offset : meta_offset + meta_length], meta_orig_length)
        if error:
            reasons.add(
                "compressed-metadata-limit" if error == "compressed-artifact-limit" else "malformed-compressed-metadata"
            )
            return reasons
        assert metadata is not None
        reasons.update(_font_xml_reasons(metadata))
        cursor = meta_offset + meta_length
    elif metadata_fields != (0, 0, 0):
        return {"malformed-compressed-artifact"}

    private_fields = (private_offset, private_length)
    if any(private_fields):
        aligned_cursor = (cursor + 3) & ~3
        if (
            not all(private_fields)
            or private_offset != aligned_cursor
            or private_offset + private_length != len(data)
            or any(data[cursor:aligned_cursor])
        ):
            reasons.add("malformed-compressed-artifact")
        else:
            reasons.add("unsupported-compressed-container")
        return reasons
    if private_fields != (0, 0) or cursor != len(data):
        reasons.add("malformed-compressed-artifact")
    return reasons


_BROTLI_DECODER: object | None = None
_BROTLI_DECODER_ATTEMPTED = False


def _brotli_decoder() -> object | None:
    global _BROTLI_DECODER, _BROTLI_DECODER_ATTEMPTED
    if _BROTLI_DECODER_ATTEMPTED:
        return _BROTLI_DECODER
    _BROTLI_DECODER_ATTEMPTED = True
    candidates = [ctypes.util.find_library("brotlidec"), "libbrotlidec.so.1", "libbrotlidec.so"]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            library = ctypes.CDLL(candidate)
            decoder = library.BrotliDecoderDecompress
            decoder.argtypes = [
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_ubyte),
                ctypes.POINTER(ctypes.c_size_t),
                ctypes.POINTER(ctypes.c_ubyte),
            ]
            decoder.restype = ctypes.c_int
        except (AttributeError, OSError):
            continue
        _BROTLI_DECODER = decoder
        return decoder
    return None


def _brotli_decompress_exact(payload: bytes, expected_length: int) -> tuple[bytes | None, str | None]:
    if expected_length > FONT_MAX_DECOMPRESSED_BYTES:
        return None, "compressed-artifact-limit"
    decoder = _brotli_decoder()
    if decoder is None:
        return None, "unsupported-compressed-container"
    encoded = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
    decoded = (ctypes.c_ubyte * max(1, expected_length))()
    decoded_size = ctypes.c_size_t(expected_length)
    result = decoder(len(payload), encoded, ctypes.byref(decoded_size), decoded)
    if result != 1 or decoded_size.value != expected_length:
        return None, "malformed-compressed-artifact"
    return bytes(decoded[:expected_length]), None


def _woff2_uint_base128(data: bytes, offset: int) -> tuple[int | None, int, str | None]:
    value = 0
    for index in range(5):
        if offset >= len(data):
            return None, offset, "malformed-compressed-artifact"
        byte = data[offset]
        offset += 1
        if index == 0 and byte == 0x80:
            return None, offset, "malformed-compressed-artifact"
        if value > (0xFFFFFFFF >> 7):
            return None, offset, "compressed-artifact-limit"
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, offset, None
    return None, offset, "malformed-compressed-artifact"


def _scan_woff2_bytes(data: bytes) -> set[str]:
    if len(data) < 48 or not data.startswith(WOFF2_SIGNATURE):
        return {"malformed-compressed-artifact"}
    try:
        (
            _signature,
            flavor,
            declared_length,
            num_tables,
            reserved,
            total_sfnt_size,
            total_compressed_size,
            _major_version,
            _minor_version,
            meta_offset,
            meta_length,
            meta_orig_length,
            private_offset,
            private_length,
        ) = struct.unpack(">4sIIHHIIHHIIIII", data[:48])
    except struct.error:
        return {"malformed-compressed-artifact"}
    if flavor not in {0x00010000, 0x4F54544F, 0x74727565, 0x74797031}:
        return {"malformed-compressed-artifact"}
    if (
        declared_length != len(data)
        or reserved != 0
        or not 0 < num_tables <= FONT_MAX_TABLES
        or total_sfnt_size > FONT_MAX_DECOMPRESSED_BYTES
        or total_compressed_size > COMPRESSED_ARTIFACT_MAX_BYTES
        or total_compressed_size == 0
    ):
        if (
            num_tables > FONT_MAX_TABLES
            or total_sfnt_size > FONT_MAX_DECOMPRESSED_BYTES
            or total_compressed_size > COMPRESSED_ARTIFACT_MAX_BYTES
        ):
            return {"compressed-artifact-limit"}
        return {"malformed-compressed-artifact"}

    offset = 48
    entries: list[tuple[bytes, int, int, bool]] = []
    tags: set[bytes] = set()
    for _index in range(num_tables):
        if offset >= len(data):
            return {"malformed-compressed-artifact"}
        flags = data[offset]
        offset += 1
        tag_index = flags & 0x3F
        transform_version = flags >> 6
        if tag_index == 0x3F:
            if offset + 4 > len(data):
                return {"malformed-compressed-artifact"}
            tag = data[offset : offset + 4]
            offset += 4
            if any(byte < 0x20 or byte > 0x7E for byte in tag):
                return {"malformed-compressed-artifact"}
        else:
            tag = WOFF2_KNOWN_TAGS[tag_index]
        if tag in tags:
            return {"malformed-compressed-artifact"}
        if tag not in FONT_SUPPORTED_TABLE_TAGS:
            return {"unsupported-compressed-container"}
        tags.add(tag)
        original_length, offset, error = _woff2_uint_base128(data, offset)
        if error:
            return {error}
        assert original_length is not None
        if original_length > FONT_MAX_TABLE_BYTES:
            return {"compressed-artifact-limit"}
        transformed = False
        if tag in {b"glyf", b"loca"}:
            if transform_version == 0:
                transformed = True
            elif transform_version != 3:
                return {"malformed-compressed-artifact"}
        elif tag == b"hmtx":
            if transform_version == 1:
                transformed = True
            elif transform_version != 0:
                return {"malformed-compressed-artifact"}
        elif transform_version != 0:
            return {"malformed-compressed-artifact"}
        transformed_length = original_length
        if transformed:
            transformed_length, offset, error = _woff2_uint_base128(data, offset)
            if error:
                return {error}
            assert transformed_length is not None
            if transformed_length > FONT_MAX_TABLE_BYTES:
                return {"compressed-artifact-limit"}
        entries.append((tag, original_length, transformed_length, transformed))

    glyf = next((entry for entry in entries if entry[0] == b"glyf"), None)
    loca = next((entry for entry in entries if entry[0] == b"loca"), None)
    if (glyf is None) != (loca is None) or (glyf and loca and (glyf[3] != loca[3] or (loca[3] and loca[2] != 0))):
        return {"malformed-compressed-artifact"}
    expected_sfnt_size = 12 + num_tables * 16 + sum((entry[1] + 3) & ~3 for entry in entries)
    if b"name" not in tags or total_sfnt_size != expected_sfnt_size:
        return {"malformed-compressed-artifact"}
    transformed_size = sum(entry[2] for entry in entries)
    if transformed_size > FONT_MAX_DECOMPRESSED_BYTES:
        return {"compressed-artifact-limit"}
    compressed_end = offset + total_compressed_size
    if compressed_end > len(data):
        return {"malformed-compressed-artifact"}
    decoded, error = _brotli_decompress_exact(data[offset:compressed_end], transformed_size)
    if error:
        return {error}
    assert decoded is not None

    reasons: set[str] = set()
    decoded_offset = 0
    for tag, _original_length, stored_length, _transformed in entries:
        table = decoded[decoded_offset : decoded_offset + stored_length]
        if len(table) != stored_length:
            return {"malformed-compressed-artifact"}
        reasons.update(_scan_metadata_bytes(table))
        if tag == b"name":
            reasons.update(_scan_sfnt_name_table(table))
        decoded_offset += stored_length
    if decoded_offset != len(decoded):
        return {"malformed-compressed-artifact"}

    cursor = (compressed_end + 3) & ~3
    if cursor > len(data) or any(data[compressed_end:cursor]):
        return {"malformed-compressed-artifact", *reasons}
    metadata_fields = (meta_offset, meta_length, meta_orig_length)
    if any(metadata_fields):
        if (
            not all(metadata_fields)
            or meta_offset != cursor
            or meta_length > COMPRESSED_METADATA_MAX_BYTES
            or meta_offset + meta_length > len(data)
            or meta_orig_length > DECOMPRESSED_METADATA_MAX_BYTES
        ):
            if meta_length > COMPRESSED_METADATA_MAX_BYTES or meta_orig_length > DECOMPRESSED_METADATA_MAX_BYTES:
                reasons.add("compressed-metadata-limit")
            else:
                reasons.add("malformed-compressed-artifact")
            return reasons
        metadata, error = _brotli_decompress_exact(data[meta_offset : meta_offset + meta_length], meta_orig_length)
        if error:
            reasons.add(
                "compressed-metadata-limit" if error == "compressed-artifact-limit" else "malformed-compressed-metadata"
            )
            return reasons
        assert metadata is not None
        reasons.update(_font_xml_reasons(metadata))
        cursor = meta_offset + meta_length
    elif metadata_fields != (0, 0, 0):
        return {"malformed-compressed-artifact"}

    private_fields = (private_offset, private_length)
    if any(private_fields):
        aligned_cursor = (cursor + 3) & ~3
        if (
            not all(private_fields)
            or private_offset != aligned_cursor
            or private_offset + private_length != len(data)
            or any(data[cursor:aligned_cursor])
        ):
            reasons.add("malformed-compressed-artifact")
        else:
            reasons.add("unsupported-compressed-container")
        return reasons
    if private_fields != (0, 0) or cursor != len(data):
        reasons.add("malformed-compressed-artifact")
    return reasons


def _scan_font_bytes(data: bytes, suffix: str) -> set[str]:
    if suffix == ".woff" and data.startswith(WOFF_SIGNATURE):
        return _scan_woff_bytes(data)
    if suffix == ".woff2" and data.startswith(WOFF2_SIGNATURE):
        return _scan_woff2_bytes(data)
    return {"malformed-compressed-artifact"}


def _scan_file(file_descriptor: int, relative_path: Path) -> set[str]:
    suffix = relative_path.suffix.casefold()
    if suffix == ".ts":
        return _scan_typescript_or_mpeg_ts(file_descriptor)
    if suffix in {".mp4", ".m4v"}:
        return _scan_iso_bmff_stream(file_descriptor)
    if suffix in TEXT_SUFFIXES or relative_path.name in TEXT_FILENAMES:
        return _scan_text_stream(file_descriptor)
    signature = os.pread(file_descriptor, 16, 0)
    if suffix in {".woff", ".woff2"} or signature.startswith((WOFF_SIGNATURE, WOFF2_SIGNATURE)):
        data, error = _read_bounded(file_descriptor, COMPRESSED_ARTIFACT_MAX_BYTES, "compressed-artifact-limit")
        return {error} if error else _scan_font_bytes(data or b"", suffix)
    if suffix in UNSUPPORTED_COMPRESSED_SUFFIXES or any(
        signature.startswith(magic) for magic in UNSUPPORTED_COMPRESSED_MAGICS
    ):
        return {"unsupported-compressed-container"}
    if signature.startswith(PNG_SIGNATURE) or suffix == ".png":
        data, error = _read_bounded(file_descriptor, COMPRESSED_ARTIFACT_MAX_BYTES, "compressed-artifact-limit")
        return {error} if error else _scan_png_bytes(data or b"")
    if signature.startswith(JPEG_SIGNATURE) or suffix in {".jpg", ".jpeg"}:
        data, error = _read_bounded(file_descriptor, COMPRESSED_ARTIFACT_MAX_BYTES, "compressed-artifact-limit")
        return {error} if error else _scan_jpeg_bytes(data or b"")
    if signature.startswith(PDF_SIGNATURE) or suffix == ".pdf":
        data, error = _read_bounded(file_descriptor, COMPRESSED_ARTIFACT_MAX_BYTES, "compressed-artifact-limit")
        return {error} if error else _scan_pdf_bytes(data or b"")
    if signature.startswith(GZIP_SIGNATURE) or suffix in {".gz", ".gzip"}:
        data, error = _read_bounded(file_descriptor, COMPRESSED_ARTIFACT_MAX_BYTES, "compressed-artifact-limit")
        return {error} if error else _scan_gzip_bytes(data or b"")
    return _scan_binary_stream(file_descriptor)


def _open_declared_root(root: Path) -> tuple[int | None, str | None]:
    """Open a declared root component-by-component without following links."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "pread"):
        return None, "unsupported-safe-traversal"
    if ".." in root.parts:
        return None, "path-traversal"
    absolute = Path(os.path.abspath(root))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        directory_fd = os.open(absolute.anchor, flags)
    except OSError:
        return None, "unreadable-directory"
    for part in absolute.parts[1:]:
        try:
            metadata = os.stat(part, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            os.close(directory_fd)
            return None, "missing-root"
        except OSError:
            os.close(directory_fd)
            return None, "unreadable-directory"
        if stat.S_ISLNK(metadata.st_mode):
            os.close(directory_fd)
            return None, "symlink"
        if not stat.S_ISDIR(metadata.st_mode):
            os.close(directory_fd)
            return None, "not-directory"
        try:
            next_fd = os.open(part, flags, dir_fd=directory_fd)
        except OSError:
            os.close(directory_fd)
            return None, "unreadable-directory"
        os.close(directory_fd)
        directory_fd = next_fd
    return directory_fd, None


def _root_identity(file_descriptor: int) -> str:
    metadata = os.fstat(file_descriptor)
    material = f"public-output-root-v1:{metadata.st_dev}:{metadata.st_ino}".encode()
    return hashlib.sha256(material).hexdigest()


def _scan_open_root(
    file_descriptor: int,
    root_label: str,
) -> tuple[str, list[Finding], dict[str, tuple[int, ...]] | None]:
    identity = _root_identity(file_descriptor)
    findings: set[Finding] = set()
    device = os.fstat(file_descriptor).st_dev
    try:
        before_inventory = tree_inventory(file_descriptor, device)
    except (OSError, ValueError):
        before_inventory = None
    _scan_directory(file_descriptor, root_label, identity, (), findings)
    try:
        after_inventory = tree_inventory(file_descriptor, device)
    except (OSError, ValueError):
        after_inventory = None
    if (before_inventory is None) != (after_inventory is None) or (
        before_inventory is not None and after_inventory is not None and before_inventory != after_inventory
    ):
        findings.add(_entry_finding(root_label, identity, Path("."), "changed-during-scan"))
    return identity, sorted(findings), after_inventory


def _entry_finding(root_label: str, root_identity: str, relative_path: Path, reason: str) -> Finding:
    safe_path = _safe_report_text(relative_path.as_posix())
    route = "/" if relative_path == Path(".") else _safe_report_text(public_route(relative_path))
    return Finding(root_label, root_identity, safe_path, route, reason)


def _scan_directory(
    directory_fd: int,
    root_label: str,
    root_identity: str,
    relative_parts: tuple[str, ...],
    findings: set[Finding],
) -> None:
    current_path = Path(*relative_parts) if relative_parts else Path(".")
    try:
        with os.scandir(directory_fd) as iterator:
            entries = sorted(iterator, key=lambda entry: entry.name)
    except OSError:
        findings.add(_entry_finding(root_label, root_identity, current_path, "unreadable-directory"))
        return

    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    for entry in entries:
        if entry.name in {"", ".", ".."} or "/" in entry.name:
            findings.add(_entry_finding(root_label, root_identity, current_path, "path-traversal"))
            continue
        parts = (*relative_parts, entry.name)
        relative_path = Path(*parts)
        safe_path = _safe_report_text(relative_path.as_posix())
        route = _safe_report_text(public_route(relative_path))
        for reason in _value_reasons(relative_path.as_posix()):
            findings.add(
                Finding(
                    root_label,
                    root_identity,
                    safe_path,
                    route,
                    "filename" if reason == "content" else reason,
                )
            )
        try:
            metadata = entry.stat(follow_symlinks=False)
        except OSError:
            findings.add(Finding(root_label, root_identity, safe_path, route, "unreadable-entry"))
            continue
        if stat.S_ISLNK(metadata.st_mode):
            findings.add(Finding(root_label, root_identity, safe_path, route, "symlink"))
            continue
        if stat.S_ISDIR(metadata.st_mode):
            try:
                child_fd = os.open(entry.name, directory_flags, dir_fd=directory_fd)
            except OSError:
                findings.add(Finding(root_label, root_identity, safe_path, route, "unreadable-directory"))
                continue
            try:
                if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                    findings.add(Finding(root_label, root_identity, safe_path, route, "special-entry"))
                else:
                    _scan_directory(child_fd, root_label, root_identity, parts, findings)
            finally:
                os.close(child_fd)
            continue
        if not stat.S_ISREG(metadata.st_mode):
            findings.add(Finding(root_label, root_identity, safe_path, route, "special-entry"))
            continue
        if metadata.st_nlink != 1:
            findings.add(Finding(root_label, root_identity, safe_path, route, "hardlink"))
            continue
        file_descriptor: int | None = None
        try:
            file_descriptor = os.open(entry.name, file_flags, dir_fd=directory_fd)
            opened_metadata = os.fstat(file_descriptor)
            if not stat.S_ISREG(opened_metadata.st_mode):
                findings.add(Finding(root_label, root_identity, safe_path, route, "special-entry"))
                continue
            if opened_metadata.st_nlink != 1:
                findings.add(Finding(root_label, root_identity, safe_path, route, "hardlink"))
                continue
            for reason in _scan_file(file_descriptor, relative_path):
                findings.add(Finding(root_label, root_identity, safe_path, route, reason))
        except OSError:
            findings.add(Finding(root_label, root_identity, safe_path, route, "unreadable-file"))
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)


def scan_root(root: Path) -> list[Finding]:
    """Return findings without following links or reading outside the root."""
    findings: set[Finding] = set()
    root_label = _safe_report_text(root.name or str(root))
    root_fd, root_error = _open_declared_root(root)
    if root_fd is None:
        findings.add(_entry_finding(root_label, "unavailable", Path("."), root_error or "unreadable-directory"))
        return sorted(findings)
    try:
        _identity, opened_findings, _inventory = _scan_open_root(root_fd, root_label)
        findings.update(opened_findings)
    finally:
        os.close(root_fd)
    return sorted(findings)


def _root_declaration(root: Path) -> dict[str, str]:
    root_fd, _error = _open_declared_root(root)
    if root_fd is None:
        identity = "unavailable"
    else:
        try:
            identity = _root_identity(root_fd)
        finally:
            os.close(root_fd)
    return {"label": _safe_report_text(root.name or str(root)), "identity": identity}


def report_payload(
    roots: list[Path],
    findings: list[Finding],
    *,
    missing: list[Path] | None = None,
    root_declarations: list[dict[str, str]] | None = None,
) -> dict:
    return {
        "schema_version": 2,
        "roots": root_declarations or [_root_declaration(root) for root in roots],
        "missing_roots": [_safe_report_text(root.name or str(root)) for root in (missing or [])],
        "routes": sorted({finding.route for finding in findings}),
        "findings": [asdict(finding) for finding in findings],
    }


def _write_report(path: Path | None, payload: dict) -> bool:
    if path is None:
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    except (OSError, TypeError, ValueError):
        return False
    return True


def _inventory_digest(root_identity: str, inventory: dict[str, tuple[int, ...]]) -> str:
    material = json.dumps(
        {
            "entries": [[path, list(state)] for path, state in sorted(inventory.items())],
            "root_identity": root_identity,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return f"sha256:{hashlib.sha256(material).hexdigest()}"


def _write_attestation(
    path: Path | None,
    *,
    root_identity: str,
    inventory: dict[str, tuple[int, ...]] | None,
) -> bool:
    if path is None:
        return True
    if inventory is None:
        return False
    payload = {
        "contract": "verdify.public-output-layout-attestation",
        "root_identity": f"sha256:{root_identity}",
        "schema_version": 1,
        "tree_digest": _inventory_digest(root_identity, inventory),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError):
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", type=Path, required=True, help="Public source/build root to scan.")
    parser.add_argument("--json-report", type=Path, help="Optional deterministic JSON report path.")
    parser.add_argument(
        "--attestation-report",
        type=Path,
        help="After one clean root scan, write a canonical identity/inventory-bound layout attestation.",
    )
    parser.add_argument(
        "--promote-to",
        type=Path,
        help="After a clean one-root scan, atomically promote that exact open candidate descriptor.",
    )
    args = parser.parse_args()

    if args.attestation_report is not None and (len(args.root) != 1 or args.promote_to is not None):
        parser.error("--attestation-report requires exactly one root and cannot be combined with promotion")

    if args.promote_to is not None:
        if len(args.root) != 1:
            parser.error("--promote-to requires exactly one --root")
        staged = Path(os.path.abspath(args.root[0]))
        live = Path(os.path.abspath(args.promote_to))
        if staged == live or staged.parent != live.parent:
            parser.error("promotion root and destination must be distinct siblings")
        parent_fd, parent_error = _open_declared_root(staged.parent)
        if parent_fd is None:
            print(
                f"public-output guard: promotion parent unavailable ({parent_error or 'unreadable-directory'})",
                file=sys.stderr,
            )
            return 2
        staged_fd: int | None = None
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            try:
                staged_fd = os.open(staged.name, flags, dir_fd=parent_fd)
            except OSError:
                print("public-output guard: promotion candidate unavailable", file=sys.stderr)
                return 2
            root_label = _safe_report_text(staged.name)
            identity, findings, inventory = _scan_open_root(staged_fd, root_label)
            if inventory is None and not findings:
                findings = [_entry_finding(root_label, identity, Path("."), "changed-during-scan")]
            declaration = {"label": root_label, "identity": identity}
            payload = report_payload([staged], findings, root_declarations=[declaration])
            if not _write_report(args.json_report, payload):
                print("public-output guard: report write failed", file=sys.stderr)
                return 2
            if findings:
                print(f"public-output guard: {len(findings)} prohibited artifact finding(s)", file=sys.stderr)
                for finding in findings:
                    print(f"  {finding.route} ({finding.root}/{finding.path}; {finding.reason})", file=sys.stderr)
                try:
                    discard_open_directory(
                        parent_fd,
                        staged.name,
                        staged_fd,
                        expected_identity=identity,
                        expected_parent_path=staged.parent,
                    )
                except (OSError, ValueError):
                    pass
                return 1
            try:
                promote_open_directory(
                    parent_fd,
                    staged.name,
                    staged_fd,
                    live.name,
                    expected_identity=identity,
                    expected_parent_path=staged.parent,
                    expected_inventory=inventory,
                )
            except (OSError, ValueError):
                print("public-output guard: descriptor-bound promotion failed", file=sys.stderr)
                return 1
            print("public-output guard: clean candidate promoted (1 root)")
            return 0
        finally:
            if staged_fd is not None:
                os.close(staged_fd)
            os.close(parent_fd)

    missing: list[Path] = []
    findings: list[Finding] = []
    declarations: list[dict[str, str]] = []
    attestation_identity = ""
    attestation_inventory: dict[str, tuple[int, ...]] | None = None
    for root in args.root:
        root_label = _safe_report_text(root.name or str(root))
        root_fd, root_error = _open_declared_root(root)
        if root_fd is None:
            declarations.append({"label": root_label, "identity": "unavailable"})
            if root_error == "missing-root":
                missing.append(root)
            else:
                findings.append(
                    _entry_finding(root_label, "unavailable", Path("."), root_error or "unreadable-directory")
                )
            continue
        try:
            identity, root_findings, inventory = _scan_open_root(root_fd, root_label)
            declarations.append({"label": root_label, "identity": identity})
            findings.extend(root_findings)
            if args.attestation_report is not None:
                attestation_identity = identity
                attestation_inventory = inventory
        finally:
            os.close(root_fd)
    if missing:
        payload = report_payload(
            args.root,
            [],
            missing=missing,
            root_declarations=declarations,
        )
        if not _write_report(args.json_report, payload):
            print("public-output guard: report write failed", file=sys.stderr)
            return 2
        for root in missing:
            print(f"public-output guard: missing root {_safe_report_text(root)}", file=sys.stderr)
        return 2

    findings = sorted(findings)
    payload = report_payload(args.root, findings, root_declarations=declarations)
    if not _write_report(args.json_report, payload):
        print("public-output guard: report write failed", file=sys.stderr)
        return 2

    if findings:
        print(f"public-output guard: {len(findings)} prohibited artifact finding(s)", file=sys.stderr)
        for finding in findings:
            print(f"  {finding.route} ({finding.root}/{finding.path}; {finding.reason})", file=sys.stderr)
        return 1
    if not _write_attestation(
        args.attestation_report,
        root_identity=attestation_identity,
        inventory=attestation_inventory,
    ):
        print("public-output guard: attestation write failed", file=sys.stderr)
        return 2
    print(f"public-output guard: clean ({len(args.root)} root(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
