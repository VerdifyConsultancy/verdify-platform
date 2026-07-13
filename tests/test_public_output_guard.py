from __future__ import annotations

import base64
import ctypes
import ctypes.util
import gzip
import importlib.util
import io
import json
import os
import signal
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import pytest

from verdify_public import output_policy as policy


def load_guard():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "check-public-output.py"
    spec = importlib.util.spec_from_file_location("public_output_guard_under_test", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_promoter():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "atomic-promote-directory.py"
    spec = importlib.util.spec_from_file_location("atomic_promoter_under_test", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", checksum)


def pdf_with_flate_stream(payload: bytes) -> bytes:
    compressed = zlib.compress(payload)
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Length "
        + str(len(compressed)).encode()
        + b" /Filter /FlateDecode >>\nstream\n"
        + compressed
        + b"\nendstream\nendobj\n%%EOF\n"
    )


def pdf_with_flate_stream_and_dictionary_padding(payload: bytes, padding: bytes) -> bytes:
    compressed = zlib.compress(payload)
    dictionary = b"<< " + padding + b" /Length " + str(len(compressed)).encode() + b" /Filter /FlateDecode >>"
    return b"%PDF-1.4\n1 0 obj\n" + dictionary + b"\nstream\n" + compressed + b"\nendstream\nendobj\n%%EOF\n"


def pdf_with_stream_dictionary(payload: bytes, dictionary: bytes) -> bytes:
    return b"%PDF-1.4\n1 0 obj\n" + dictionary + b"\nstream\n" + payload + b"\nendstream\nendobj\n%%EOF\n"


def gzip_member(
    payload: bytes,
    *,
    filename: bytes = b"",
    comment: bytes = b"",
    extra: bytes = b"",
    header_crc: bool = False,
) -> bytes:
    canonical = gzip.compress(payload, mtime=0)
    flags = (0x04 if extra else 0) | (0x08 if filename else 0) | (0x10 if comment else 0) | (0x02 if header_crc else 0)
    header = bytearray(b"\x1f\x8b\x08" + bytes([flags]) + b"\x00\x00\x00\x00\x00\xff")
    if extra:
        header.extend(struct.pack("<H", len(extra)))
        header.extend(extra)
    if filename:
        header.extend(filename + b"\x00")
    if comment:
        header.extend(comment + b"\x00")
    if header_crc:
        header.extend(struct.pack("<H", zlib.crc32(header) & 0xFFFF))
    return bytes(header) + canonical[10:]


def mpeg_ts_crc32(payload: bytes) -> int:
    value = 0xFFFFFFFF
    for byte in payload:
        value ^= byte << 24
        for _ in range(8):
            if value & 0x80000000:
                value = ((value << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
            else:
                value = (value << 1) & 0xFFFFFFFF
    return value


def mpeg_ts_section(table_id: int, body: bytes) -> bytes:
    section_length = len(body) + 4
    section = bytes([table_id, 0xB0 | (section_length >> 8), section_length & 0xFF]) + body
    return section + mpeg_ts_crc32(section).to_bytes(4, "big")


def mpeg_ts_packet(pid: int, payload: bytes, *, payload_unit_start: bool = False, counter: int = 0) -> bytes:
    assert 0 <= pid < 0x2000
    assert len(payload) <= 184
    second = (0x40 if payload_unit_start else 0) | (pid >> 8)
    if len(payload) == 184:
        return bytes([0x47, second, pid & 0xFF, 0x10 | counter]) + payload
    adaptation_length = 183 - len(payload)
    adaptation = b"" if adaptation_length == 0 else b"\x00" + b"\xff" * (adaptation_length - 1)
    return bytes([0x47, second, pid & 0xFF, 0x30 | counter, adaptation_length]) + adaptation + payload


def mpeg_ts_adaptation_packet(
    pid: int,
    payload: bytes,
    adaptation_content: bytes,
    *,
    payload_unit_start: bool = False,
    counter: int = 0,
) -> bytes:
    assert 0 <= pid < 0x2000
    adaptation_length = 183 - len(payload)
    assert 0 < len(adaptation_content) <= adaptation_length
    second = (0x40 if payload_unit_start else 0) | (pid >> 8)
    adaptation = adaptation_content + b"\xff" * (adaptation_length - len(adaptation_content))
    return bytes([0x47, second, pid & 0xFF, 0x30 | counter, adaptation_length]) + adaptation + payload


def mpeg_ts_fixture(
    *,
    metadata: bytes | None = None,
    split_pmt: bool = False,
    pcr_pid: int | None = None,
    video_stream_type: int = 0x1B,
    video_payload: bytes = b"ordinary video payload",
) -> bytes:
    program = 1
    pmt_pid = 0x1000
    video_pid = 0x0100
    metadata_pid = 0x0102
    pcr_pid = video_pid if pcr_pid is None else pcr_pid
    assert 0 <= pcr_pid < 0x2000
    assert 0 <= video_stream_type <= 0xFF
    pat = mpeg_ts_section(
        0x00,
        b"\x00\x01\xc1\x00\x00" + bytes([program >> 8, program & 0xFF, 0xE0 | (pmt_pid >> 8), pmt_pid & 0xFF]),
    )
    streams = bytes([video_stream_type, 0xE0 | (video_pid >> 8), video_pid & 0xFF, 0xF0, 0x00])
    if metadata is not None:
        streams += bytes([0x15, 0xE0 | (metadata_pid >> 8), metadata_pid & 0xFF, 0xF0, 0x00])
    pmt = mpeg_ts_section(
        0x02,
        bytes(
            [
                program >> 8,
                program & 0xFF,
                0xC1,
                0x00,
                0x00,
                0xE0 | (pcr_pid >> 8),
                pcr_pid & 0xFF,
                0xF0,
                0x00,
            ]
        )
        + streams,
    )
    pmt_packets = [mpeg_ts_packet(pmt_pid, b"\x00" + pmt, payload_unit_start=True)]
    if split_pmt:
        pmt_packets = [
            mpeg_ts_packet(pmt_pid, b"\x00" + pmt[:5], payload_unit_start=True),
            mpeg_ts_packet(pmt_pid, pmt[5:10], counter=1),
            mpeg_ts_packet(pmt_pid, pmt[10:], counter=2),
        ]
    packets = [
        mpeg_ts_packet(0, b"\x00" + pat, payload_unit_start=True),
        *pmt_packets,
        mpeg_ts_packet(video_pid, b"\x00\x00\x01\xe0" + video_payload, payload_unit_start=True),
    ]
    if metadata is not None:
        packets.append(
            mpeg_ts_packet(
                metadata_pid,
                b"\x00\x00\x01\xbdID3" + metadata,
                payload_unit_start=True,
            )
        )
    return b"".join(packets)


def iso_bmff_box(box_type: bytes, payload: bytes) -> bytes:
    assert len(box_type) == 4
    return struct.pack(">I4s", len(payload) + 8, box_type) + payload


def iso_bmff_fixture(
    *,
    metadata: bytes = b"ordinary metadata",
    media: bytes = b"compressed codec bytes",
    unreferenced: bytes = b"",
) -> bytes:
    ftyp = iso_bmff_box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2")
    udta = iso_bmff_box(b"udta", metadata)

    def moov(chunk_offset: int) -> bytes:
        stsd = iso_bmff_box(b"stsd", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + iso_bmff_box(b"avc1", b""))
        stsc = iso_bmff_box(b"stsc", b"\x00\x00\x00\x00" + struct.pack(">IIII", 1, 1, 1, 1))
        stsz = iso_bmff_box(b"stsz", b"\x00\x00\x00\x00" + struct.pack(">II", len(media), 1))
        stco = iso_bmff_box(b"stco", b"\x00\x00\x00\x00" + struct.pack(">II", 1, chunk_offset))
        stbl = iso_bmff_box(b"stbl", stsd + stsc + stsz + stco)
        minf = iso_bmff_box(b"minf", stbl)
        hdlr = iso_bmff_box(b"hdlr", b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00vide" + b"\x00" * 12)
        return iso_bmff_box(b"moov", iso_bmff_box(b"trak", iso_bmff_box(b"mdia", hdlr + minf)))

    preliminary_moov = moov(0)
    media_offset = len(ftyp) + len(preliminary_moov) + len(udta) + 8
    return ftyp + moov(media_offset) + udta + iso_bmff_box(b"mdat", media + unreferenced)


def iso_bmff_many_mdat_fixture(*, sample_count: int = 100_000, empty_mdat_count: int = 4093) -> bytes:
    ftyp = iso_bmff_box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2")
    empty_mdats = iso_bmff_box(b"mdat", b"") * empty_mdat_count

    def moov(offset_bytes: bytes) -> bytes:
        stsd = iso_bmff_box(b"stsd", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + iso_bmff_box(b"avc1", b""))
        stsc = iso_bmff_box(b"stsc", b"\x00\x00\x00\x00" + struct.pack(">IIII", 1, 1, 1, 1))
        stsz = iso_bmff_box(b"stsz", b"\x00\x00\x00\x00" + struct.pack(">II", 1, sample_count))
        stco = iso_bmff_box(b"stco", b"\x00\x00\x00\x00" + struct.pack(">I", sample_count) + offset_bytes)
        stbl = iso_bmff_box(b"stbl", stsd + stsc + stsz + stco)
        minf = iso_bmff_box(b"minf", stbl)
        hdlr = iso_bmff_box(b"hdlr", b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00vide" + b"\x00" * 12)
        return iso_bmff_box(b"moov", iso_bmff_box(b"trak", iso_bmff_box(b"mdia", hdlr + minf)))

    preliminary_moov = moov(b"\x00" * (sample_count * 4))
    media_offset = len(ftyp) + len(preliminary_moov) + len(empty_mdats) + 8
    offset_bytes = struct.pack(f">{sample_count}I", *range(media_offset, media_offset + sample_count))
    return ftyp + moov(offset_bytes) + empty_mdats + iso_bmff_box(b"mdat", b"\x80" * sample_count)


def iso_bmff_multi_track_fixture(*, samples_per_track: int, track_count: int) -> bytes:
    ftyp = iso_bmff_box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2")

    def track(chunk_offset: int) -> bytes:
        stsd = iso_bmff_box(b"stsd", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + iso_bmff_box(b"avc1", b""))
        stsc = iso_bmff_box(
            b"stsc",
            b"\x00\x00\x00\x00" + struct.pack(">IIII", 1, 1, samples_per_track, 1),
        )
        stsz = iso_bmff_box(
            b"stsz",
            b"\x00\x00\x00\x00" + struct.pack(">II", 1, samples_per_track),
        )
        stco = iso_bmff_box(b"stco", b"\x00\x00\x00\x00" + struct.pack(">II", 1, chunk_offset))
        stbl = iso_bmff_box(b"stbl", stsd + stsc + stsz + stco)
        minf = iso_bmff_box(b"minf", stbl)
        hdlr = iso_bmff_box(b"hdlr", b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00vide" + b"\x00" * 12)
        return iso_bmff_box(b"trak", iso_bmff_box(b"mdia", hdlr + minf))

    preliminary_moov = iso_bmff_box(b"moov", b"".join(track(0) for _ in range(track_count)))
    media_offset = len(ftyp) + len(preliminary_moov) + 8
    moov = iso_bmff_box(
        b"moov",
        b"".join(track(media_offset + index * samples_per_track) for index in range(track_count)),
    )
    return ftyp + moov + iso_bmff_box(b"mdat", b"\x80" * (samples_per_track * track_count))


def font_table_checksum(tag: bytes, payload: bytes) -> int:
    if tag == b"head" and len(payload) >= 12:
        payload = payload[:8] + b"\x00\x00\x00\x00" + payload[12:]
    payload += b"\x00" * (-len(payload) % 4)
    return sum(struct.unpack(f">{len(payload) // 4}I", payload)) & 0xFFFFFFFF if payload else 0


def font_name_table(text: str) -> bytes:
    value = text.encode("utf-16-be")
    record = struct.pack(">HHHHHH", 3, 1, 0x0409, 1, len(value), 0)
    return struct.pack(">HHH", 0, 1, 6 + len(record)) + record + value


def woff_fixture(
    text: str = "ordinary public font",
    *,
    metadata: bytes | None = None,
    private: bytes | None = None,
    extra_tables: dict[bytes, bytes] | None = None,
) -> bytes:
    tables = {b"name": font_name_table(text), **(extra_tables or {})}
    ordered = sorted(tables.items())
    directory_end = 44 + len(ordered) * 20
    cursor = directory_end
    directory = bytearray()
    body = bytearray()
    for tag, payload in ordered:
        compressed = zlib.compress(payload)
        stored = compressed if len(compressed) < len(payload) else payload
        directory.extend(
            struct.pack(">4sIIII", tag, cursor, len(stored), len(payload), font_table_checksum(tag, payload))
        )
        body.extend(stored)
        body.extend(b"\x00" * (-len(body) % 4))
        cursor = directory_end + len(body)

    metadata_offset = metadata_length = metadata_original_length = 0
    if metadata is not None:
        encoded_metadata = zlib.compress(metadata)
        metadata_offset = cursor
        metadata_length = len(encoded_metadata)
        metadata_original_length = len(metadata)
        body.extend(encoded_metadata)
        cursor += len(encoded_metadata)

    private_offset = private_length = 0
    if private is not None:
        padding = -cursor % 4
        body.extend(b"\x00" * padding)
        cursor += padding
        private_offset = cursor
        private_length = len(private)
        body.extend(private)
        cursor += len(private)

    total_sfnt_size = 12 + len(ordered) * 16 + sum((len(payload) + 3) & ~3 for _, payload in ordered)
    header = struct.pack(
        ">4sIIHHIHHIIIII",
        b"wOFF",
        0x00010000,
        cursor,
        len(ordered),
        0,
        total_sfnt_size,
        1,
        0,
        metadata_offset,
        metadata_length,
        metadata_original_length,
        private_offset,
        private_length,
    )
    return header + bytes(directory) + bytes(body)


def woff2_uint_base128(value: int) -> bytes:
    assert 0 <= value <= 0xFFFFFFFF
    encoded = bytearray([value & 0x7F])
    value >>= 7
    while value:
        encoded.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(encoded))


def brotli_compress(payload: bytes) -> bytes:
    library_name = ctypes.util.find_library("brotlienc")
    assert library_name, "Brotli encoder library is required for WOFF2 guard tests"
    library = ctypes.CDLL(library_name)
    max_size = library.BrotliEncoderMaxCompressedSize
    max_size.argtypes = [ctypes.c_size_t]
    max_size.restype = ctypes.c_size_t
    compress = library.BrotliEncoderCompress
    compress.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_ubyte),
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_ubyte),
    ]
    compress.restype = ctypes.c_int
    source = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
    capacity = max_size(len(payload))
    output = (ctypes.c_ubyte * capacity)()
    output_size = ctypes.c_size_t(capacity)
    assert compress(5, 22, 0, len(payload), source, ctypes.byref(output_size), output) == 1
    return bytes(output[: output_size.value])


def woff2_fixture(
    text: str = "ordinary public font",
    *,
    metadata: bytes | None = None,
    private: bytes | None = None,
    extra_tables: dict[bytes, bytes] | None = None,
) -> bytes:
    tables = {b"name": font_name_table(text), **(extra_tables or {})}
    ordered = sorted(tables.items())
    directory = bytearray()
    transformed = bytearray()
    for tag, payload in ordered:
        if tag == b"name":
            directory.append(5)
        else:
            directory.append(0x3F)
            directory.extend(tag)
        directory.extend(woff2_uint_base128(len(payload)))
        transformed.extend(payload)

    compressed = brotli_compress(bytes(transformed))
    body = bytearray(compressed)
    body.extend(b"\x00" * (-(48 + len(directory) + len(body)) % 4))
    cursor = 48 + len(directory) + len(body)

    metadata_offset = metadata_length = metadata_original_length = 0
    if metadata is not None:
        encoded_metadata = brotli_compress(metadata)
        metadata_offset = cursor
        metadata_length = len(encoded_metadata)
        metadata_original_length = len(metadata)
        body.extend(encoded_metadata)
        cursor += len(encoded_metadata)

    private_offset = private_length = 0
    if private is not None:
        padding = -cursor % 4
        body.extend(b"\x00" * padding)
        cursor += padding
        private_offset = cursor
        private_length = len(private)
        body.extend(private)
        cursor += len(private)

    total_sfnt_size = 12 + len(ordered) * 16 + sum((len(payload) + 3) & ~3 for _, payload in ordered)
    header = struct.pack(
        ">4sIIHHIIHHIIIII",
        b"wOF2",
        0x00010000,
        cursor,
        len(ordered),
        0,
        total_sfnt_size,
        len(compressed),
        1,
        0,
        metadata_offset,
        metadata_length,
        metadata_original_length,
        private_offset,
        private_length,
    )
    return header + bytes(directory) + bytes(body)


def test_guard_enumerates_routes_and_never_echoes_prohibited_text(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    plan = tmp_path / "plans" / "2026-07-03.md"
    plan.parent.mkdir()
    plan.write_text(f"public text mentioning {excluded}\n", encoding="utf-8")
    named_asset = tmp_path / "static" / f"{excluded}-observation.txt"
    named_asset.parent.mkdir()
    named_asset.write_text("safe body", encoding="utf-8")
    (tmp_path / "allowed.md").write_text("canna and cannabinoid remain public", encoding="utf-8")

    findings = guard.scan_root(tmp_path)
    payload = guard.report_payload([tmp_path], findings)

    assert payload["routes"] == ["/plans/2026-07-03", f"/static/{policy.PUBLIC_CROP_REDACTION}-observation.txt"]
    assert excluded not in str(payload).casefold()
    assert {finding.reason for finding in findings} == {"content", "filename"}


def test_ts_suffix_preserves_typescript_and_validates_mpeg_transport_stream(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    (tmp_path / "types.ts").write_text("export type ClimateSample = { value: number };\n", encoding="utf-8")
    (tmp_path / "protected.ts").write_text(f'export const crop = "{excluded}";\n', encoding="utf-8")
    (tmp_path / "protected-utf16.ts").write_bytes(f'export const crop = "{excluded}";\n'.encode("utf-16"))
    (tmp_path / "segment.ts").write_bytes(mpeg_ts_fixture())
    (tmp_path / "split-map.ts").write_bytes(mpeg_ts_fixture(split_pmt=True))

    findings = guard.scan_root(tmp_path)

    assert [(finding.path, finding.reason) for finding in findings] == [
        ("protected-utf16.ts", "content"),
        ("protected-utf16.ts", "unreadable-text"),
        ("protected.ts", "content"),
    ]
    assert excluded not in str(findings).casefold()


def test_mpeg_transport_stream_scans_metadata_and_enforces_its_bound(tmp_path, monkeypatch):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    (tmp_path / "protected.ts").write_bytes(mpeg_ts_fixture(metadata=f"review {excluded}".encode()))
    (tmp_path / "invalid.ts").write_bytes(mpeg_ts_fixture(metadata=b"humidity: NaN%"))

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {
        ("invalid.ts", "invalid-rendered-value"),
        ("protected.ts", "content"),
    }
    assert excluded not in str(findings).casefold()

    bounded = tmp_path / "bounded.ts"
    bounded.write_bytes(mpeg_ts_fixture(metadata=b"ordinary metadata"))
    monkeypatch.setattr(guard, "MPEG_TS_METADATA_MAX_BYTES", 20)
    bounded_findings = guard.scan_root(tmp_path)
    assert {finding.path for finding in bounded_findings if finding.reason == "media-metadata-limit"} == {
        "bounded.ts",
        "invalid.ts",
        "protected.ts",
    }


def test_mpeg_transport_stream_rejects_packet_crc_header_and_map_corruption(tmp_path):
    guard = load_guard()
    valid = mpeg_ts_fixture()
    corruptions: dict[str, bytes] = {}

    bad_sync = bytearray(valid)
    bad_sync[guard.MPEG_TS_PACKET_SIZE] = 0x46
    corruptions["bad-sync.ts"] = bytes(bad_sync)

    bad_header = bytearray(valid)
    bad_header[guard.MPEG_TS_PACKET_SIZE * 2 + 3] = 0
    corruptions["bad-header.ts"] = bytes(bad_header)

    bad_crc = bytearray(valid)
    bad_crc[guard.MPEG_TS_PACKET_SIZE - 1] ^= 0x01
    corruptions["bad-crc.ts"] = bytes(bad_crc)

    missing_map = bytearray(valid)
    missing_map[guard.MPEG_TS_PACKET_SIZE + 1] = 0x41
    missing_map[guard.MPEG_TS_PACKET_SIZE + 2] = 0x00
    corruptions["missing-map.ts"] = bytes(missing_map)
    corruptions["truncated.ts"] = valid[:-1]

    for name, payload in corruptions.items():
        (tmp_path / name).write_bytes(payload)

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {
        (name, "malformed-media-artifact") for name in corruptions
    }


def test_mpeg_transport_stream_rejects_payload_before_map_and_undeclared_pids(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    valid = mpeg_ts_fixture(metadata=f"review {excluded}".encode())
    packets = [valid[offset : offset + guard.MPEG_TS_PACKET_SIZE] for offset in range(0, len(valid), 188)]
    assert len(packets) == 4

    # PAT, metadata, PMT, video: the metadata PID is not classifiable until
    # the PMT arrives and must not disappear from the policy scan.
    (tmp_path / "pre-map.ts").write_bytes(b"".join((packets[0], packets[3], packets[1], packets[2])))
    # A payload PID absent from the validated PMT is equally ambiguous.
    undeclared = mpeg_ts_packet(0x0103, f"review {excluded}".encode(), payload_unit_start=True)
    (tmp_path / "undeclared.ts").write_bytes(b"".join((packets[0], packets[1], undeclared, packets[2])))

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {
        ("pre-map.ts", "malformed-media-artifact"),
        ("undeclared.ts", "malformed-media-artifact"),
    }


def test_mpeg_transport_stream_rejects_null_payload_and_undeclared_pcr(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    protected = f"review {excluded}".encode()
    valid = mpeg_ts_fixture()

    (tmp_path / "null-payload.ts").write_bytes(valid + mpeg_ts_packet(0x1FFF, protected))
    undeclared_pcr = 0x0103
    (tmp_path / "undeclared-pcr.ts").write_bytes(
        mpeg_ts_fixture(pcr_pid=undeclared_pcr) + mpeg_ts_packet(undeclared_pcr, protected, payload_unit_start=True)
    )
    # Canonical null-packet stuffing and a PMT's explicit null-PCR sentinel
    # remain valid inputs.
    (tmp_path / "padding.ts").write_bytes(valid + mpeg_ts_packet(0x1FFF, b"\xff" * 184))
    (tmp_path / "null-pcr.ts").write_bytes(mpeg_ts_fixture(pcr_pid=0x1FFF))

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {
        ("null-payload.ts", "malformed-media-artifact"),
        ("undeclared-pcr.ts", "malformed-media-artifact"),
    }


def test_mpeg_transport_stream_rejects_unknown_declared_stream_type(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    (tmp_path / "unknown-invalid.ts").write_bytes(
        mpeg_ts_fixture(video_stream_type=0x99, video_payload=b"humidity: NaN%")
    )
    (tmp_path / "unknown-protected.ts").write_bytes(
        mpeg_ts_fixture(video_stream_type=0x99, video_payload=f"review {excluded}".encode())
    )

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {
        ("unknown-invalid.ts", "malformed-media-artifact"),
        ("unknown-protected.ts", "malformed-media-artifact"),
    }


def test_mpeg_transport_stream_validates_and_scans_adaptation_optional_fields(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    protected = f"review {excluded}".encode()
    invalid = b"humidity: NaN%"
    valid_prefix = mpeg_ts_fixture()[: 2 * guard.MPEG_TS_PACKET_SIZE]
    video_payload = b"\x00\x00\x01\xe0ordinary video payload"

    def segment(adaptation_content: bytes) -> bytes:
        return valid_prefix + mpeg_ts_adaptation_packet(
            0x0100,
            video_payload,
            adaptation_content,
            payload_unit_start=True,
        )

    protected_extension = b"\x1f" + protected
    invalid_extension = b"\x1f" + invalid
    (tmp_path / "protected-extension.ts").write_bytes(
        segment(b"\x01" + bytes([len(protected_extension)]) + protected_extension)
    )
    (tmp_path / "invalid-extension.ts").write_bytes(
        segment(b"\x01" + bytes([len(invalid_extension)]) + invalid_extension)
    )
    (tmp_path / "invalid-private.ts").write_bytes(segment(b"\x02" + bytes([len(invalid)]) + invalid))
    (tmp_path / "invalid-pcr.ts").write_bytes(segment(b"\x10humid!"))
    (tmp_path / "valid-extension.ts").write_bytes(segment(b"\x01\x04\x1f\xff\xff\xff"))

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {
        ("invalid-extension.ts", "malformed-media-artifact"),
        ("invalid-pcr.ts", "malformed-media-artifact"),
        ("invalid-private.ts", "invalid-rendered-value"),
        ("protected-extension.ts", "malformed-media-artifact"),
    }
    assert excluded not in str(findings).casefold()


def test_mpeg_transport_stream_rejects_unsupported_and_ambiguous_packet_layouts(tmp_path):
    guard = load_guard()
    valid = mpeg_ts_fixture()
    m2ts = b"".join(b"\x00\x00\x00\x00" + valid[offset : offset + 188] for offset in range(0, len(valid), 188))
    (tmp_path / "wrapped.ts").write_bytes(m2ts)

    text_media_ambiguity = bytearray(b"a" * (188 * guard.MPEG_TS_PROBE_PACKETS))
    for offset in range(0, len(text_media_ambiguity), 188):
        text_media_ambiguity[offset] = 0x47
    (tmp_path / "ambiguous.ts").write_bytes(text_media_ambiguity)

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {
        ("ambiguous.ts", "ambiguous-media-artifact"),
        ("wrapped.ts", "unsupported-media-packet-size"),
    }


def test_iso_bmff_scans_metadata_and_rejects_unproven_mdat_gaps(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    (tmp_path / "protected.mp4").write_bytes(iso_bmff_fixture(metadata=f"review {excluded}".encode()))
    (tmp_path / "invalid.m4v").write_bytes(iso_bmff_fixture(metadata=b"humidity: NaN%"))
    (tmp_path / "protected-mdat.mp4").write_bytes(iso_bmff_fixture(unreferenced=f"review {excluded}".encode()))
    encoded = base64.urlsafe_b64encode(f"review {excluded}".encode()).rstrip(b"=")
    (tmp_path / "encoded-mdat.mp4").write_bytes(iso_bmff_fixture(unreferenced=encoded))
    (tmp_path / "utf16-mdat.mp4").write_bytes(iso_bmff_fixture(unreferenced=f"review {excluded}".encode("utf-16-le")))
    (tmp_path / "invalid-mdat.m4v").write_bytes(iso_bmff_fixture(unreferenced=b"humidity: NaN%"))
    (tmp_path / "short-invalid-mdat.mp4").write_bytes(iso_bmff_fixture(unreferenced=b"NaN"))
    (tmp_path / "gzip-mdat.mp4").write_bytes(
        iso_bmff_fixture(unreferenced=gzip.compress(f"review {excluded}".encode()))
    )
    (tmp_path / "opaque-mdat.mp4").write_bytes(iso_bmff_fixture(unreferenced=b"\x01\x02\x03\x04"))
    (tmp_path / "oversized-padding.mp4").write_bytes(
        iso_bmff_fixture(unreferenced=b"\x00" * (guard.ISO_BMFF_MAX_GAP_PADDING_BYTES + 1))
    )
    (tmp_path / "valid-padding.mp4").write_bytes(
        iso_bmff_fixture(unreferenced=b"\x00" * guard.ISO_BMFF_MAX_GAP_PADDING_BYTES)
    )
    (tmp_path / "safe.mp4").write_bytes(iso_bmff_fixture(media=(b"$-information-none-is-codec-data" * 65_536)))
    (tmp_path / "init.mp4").write_bytes(
        iso_bmff_box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2") + iso_bmff_box(b"moov", b"ordinary metadata")
    )

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {
        ("encoded-mdat.mp4", "malformed-media-artifact"),
        ("gzip-mdat.mp4", "malformed-media-artifact"),
        ("invalid-mdat.m4v", "malformed-media-artifact"),
        ("invalid.m4v", "invalid-rendered-value"),
        ("opaque-mdat.mp4", "malformed-media-artifact"),
        ("oversized-padding.mp4", "malformed-media-artifact"),
        ("protected-mdat.mp4", "malformed-media-artifact"),
        ("protected.mp4", "content"),
        ("short-invalid-mdat.mp4", "malformed-media-artifact"),
        ("utf16-mdat.mp4", "malformed-media-artifact"),
    }
    assert excluded not in str(findings).casefold()


def test_iso_bmff_rejects_malformed_boxes_and_metadata_bounds(tmp_path, monkeypatch):
    guard = load_guard()
    malformed = bytearray(iso_bmff_fixture())
    malformed[0:4] = struct.pack(">I", len(malformed) + 1)
    (tmp_path / "malformed.mp4").write_bytes(malformed)
    (tmp_path / "malformed-ftyp.mp4").write_bytes(
        iso_bmff_box(b"ftyp", b"isom") + iso_bmff_box(b"moov", b"ordinary metadata") + iso_bmff_box(b"mdat", b"codec")
    )
    (tmp_path / "bounded.mp4").write_bytes(iso_bmff_fixture(metadata=b"ordinary metadata"))
    monkeypatch.setattr(guard, "ISO_BMFF_MAX_METADATA_BYTES", 8)

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {
        ("bounded.mp4", "media-metadata-limit"),
        ("malformed-ftyp.mp4", "malformed-media-artifact"),
        ("malformed.mp4", "malformed-media-artifact"),
    }


def test_iso_bmff_rejects_unproven_codec_and_out_of_bounds_sample_table(tmp_path):
    guard = load_guard()
    unknown_codec = iso_bmff_fixture().replace(b"avc1", b"text", 1)
    (tmp_path / "unknown-codec.mp4").write_bytes(unknown_codec)

    outside = bytearray(iso_bmff_fixture())
    stco_type = outside.index(b"stco")
    outside[stco_type + 12 : stco_type + 16] = struct.pack(">I", len(outside) + 1)
    (tmp_path / "outside-mdat.mp4").write_bytes(outside)

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {
        ("outside-mdat.mp4", "malformed-media-artifact"),
        ("unknown-codec.mp4", "malformed-media-artifact"),
    }


def test_iso_bmff_many_mdat_sample_validation_is_linear(tmp_path):
    guard = load_guard()
    artifact = tmp_path / "many-mdats.mp4"
    artifact.write_bytes(iso_bmff_many_mdat_fixture())

    started = time.perf_counter()
    findings = guard.scan_root(tmp_path)
    elapsed = time.perf_counter() - started

    assert findings == []
    # The former nested containment and gap walks took more than 20 seconds
    # for this 100k-sample/4094-mdat shape. Five seconds leaves substantial CI
    # noise headroom while preserving a practical regression gate.
    assert elapsed < 5.0


def test_iso_bmff_sample_budget_is_aggregate_across_tracks(tmp_path, monkeypatch):
    guard = load_guard()
    artifact = tmp_path / "aggregate-sample-budget.mp4"
    artifact.write_bytes(iso_bmff_multi_track_fixture(samples_per_track=3, track_count=2))
    monkeypatch.setattr(guard, "ISO_BMFF_MAX_SAMPLES", 4)

    findings = guard.scan_root(tmp_path)

    assert [(finding.path, finding.reason) for finding in findings] == [
        ("aggregate-sample-budget.mp4", "malformed-media-artifact"),
    ]


def test_guard_maps_index_pages_and_scans_binary_metadata(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    index = tmp_path / "reference" / "index.html"
    index.parent.mkdir()
    index.write_text("clean", encoding="utf-8")
    binary = tmp_path / "video.bin"
    binary.write_bytes(b"\x00\xff" + excluded.encode())

    assert guard.public_route(index.relative_to(tmp_path)) == "/reference"
    findings = guard.scan_root(tmp_path)
    assert [(finding.path, finding.reason) for finding in findings] == [("video.bin", "content")]


def test_guard_scans_bounded_compressed_png_metadata(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    protected_payload = b"Comment\x00\x00" + zlib.compress(f"review {excluded}".encode("latin-1"))
    invalid_payload = b"Metrics\x00\x00" + zlib.compress(b"humidity: NaN%")
    png = (
        guard.PNG_SIGNATURE
        + png_chunk(b"zTXt", protected_payload)
        + png_chunk(b"zTXt", invalid_payload)
        + png_chunk(b"IEND", b"")
    )
    (tmp_path / "metadata.png").write_bytes(png)

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {
        ("metadata.png", "content"),
        ("metadata.png", "invalid-rendered-value"),
    }
    assert excluded not in str(findings).casefold()


def test_guard_rejects_png_metadata_decompression_bombs_at_a_fixed_limit(tmp_path):
    guard = load_guard()
    compressed = zlib.compress(b"x" * (guard.DECOMPRESSED_METADATA_MAX_BYTES + 1), level=9)
    payload = b"Comment\x00\x00" + compressed
    png = guard.PNG_SIGNATURE + png_chunk(b"zTXt", payload) + png_chunk(b"IEND", b"")
    (tmp_path / "bounded.png").write_bytes(png)

    findings = guard.scan_root(tmp_path)

    assert [(finding.path, finding.reason) for finding in findings] == [("bounded.png", "compressed-metadata-limit")]


def test_guard_accepts_uncompressed_itxt_and_checked_in_images():
    guard = load_guard()
    itxt = b"Description\x00\x00\x00en\x00Description\x00ordinary public image"
    png = guard.PNG_SIGNATURE + png_chunk(b"iTXt", itxt) + png_chunk(b"IEND", b"")

    assert guard._scan_png_bytes(png) == set()
    repo_root = Path(__file__).resolve().parents[1]
    assert guard.scan_root(repo_root / "site" / "docs" / "images") == []
    assert guard.scan_root(repo_root / "site" / "quartz" / "static") == []


def test_png_iccp_exif_and_unknown_ancillary_metadata_fail_closed():
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS)).encode()
    iccp_name = (
        guard.PNG_SIGNATURE
        + png_chunk(
            b"iCCP",
            excluded + b" profile\x00\x00" + zlib.compress(b"ordinary profile"),
        )
        + png_chunk(b"IEND", b"")
    )
    iccp_body = (
        guard.PNG_SIGNATURE
        + png_chunk(
            b"iCCP",
            b"Public profile\x00\x00" + zlib.compress(b"profile note " + excluded),
        )
        + png_chunk(b"IEND", b"")
    )
    exif = guard.PNG_SIGNATURE + png_chunk(b"eXIf", b"camera note " + excluded) + png_chunk(b"IEND", b"")
    unknown = guard.PNG_SIGNATURE + png_chunk(b"vpAg", b"opaque application metadata") + png_chunk(b"IEND", b"")

    assert "content" in guard._scan_png_bytes(iccp_name)
    assert "content" in guard._scan_png_bytes(iccp_body)
    assert "content" in guard._scan_png_bytes(exif)
    assert guard._scan_png_bytes(unknown) == {"unsupported-compressed-container"}


def test_png_textual_metadata_parse_and_size_bounds_fail_closed(monkeypatch):
    guard = load_guard()
    monkeypatch.setattr(guard, "COMPRESSED_METADATA_MAX_BYTES", 16)
    oversized = guard.PNG_SIGNATURE + png_chunk(b"tEXt", b"Comment\x00" + b"x" * 32) + png_chunk(b"IEND", b"")
    malformed_iccp = guard.PNG_SIGNATURE + png_chunk(b"iCCP", b"Profile\x00\x01broken") + png_chunk(b"IEND", b"")

    assert guard._scan_png_bytes(oversized) == {"compressed-metadata-limit"}
    assert guard._scan_png_bytes(malformed_iccp) == {"malformed-compressed-metadata"}


def test_checked_in_site_extension_inventory_is_explicitly_supported():
    guard = load_guard()
    site = Path(__file__).resolve().parents[1] / "site"
    unsupported = []
    for path in site.rglob("*"):
        if not path.is_file():
            continue
        if (
            path.name not in guard.TEXT_FILENAMES
            and path.suffix.casefold() not in guard.TEXT_SUFFIXES
            and path.suffix.casefold() not in guard.SUPPORTED_BINARY_SUFFIXES
        ):
            unsupported.append(path.relative_to(site).as_posix())
    assert unsupported == []


def test_guard_scans_woff_and_woff2_name_and_xml_metadata(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    safe_metadata = b'<?xml version="1.0" encoding="UTF-8"?><metadata><text>ordinary font</text></metadata>'
    protected_metadata = f"<metadata><text>review {excluded}</text></metadata>".encode()
    invalid_metadata = b"<metadata><text>humidity: NaN%</text></metadata>"

    (tmp_path / "safe.woff").write_bytes(woff_fixture(metadata=safe_metadata))
    (tmp_path / "safe.woff2").write_bytes(woff2_fixture(metadata=safe_metadata))
    (tmp_path / "protected-name.woff").write_bytes(woff_fixture(f"review {excluded}"))
    (tmp_path / "protected-name.woff2").write_bytes(woff2_fixture(f"review {excluded}"))
    (tmp_path / "protected-metadata.woff").write_bytes(woff_fixture(metadata=protected_metadata))
    (tmp_path / "invalid-metadata.woff2").write_bytes(woff2_fixture(metadata=invalid_metadata))

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {
        ("invalid-metadata.woff2", "invalid-rendered-value"),
        ("protected-metadata.woff", "content"),
        ("protected-name.woff", "content"),
        ("protected-name.woff2", "content"),
    }
    assert excluded not in str(findings).casefold()


def test_font_guard_scans_supported_tables_and_rejects_opaque_tables_and_private_data():
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS)).encode()

    assert "content" in guard._scan_woff_bytes(woff_fixture(extra_tables={b"post": b"review " + excluded}))
    assert "content" in guard._scan_woff2_bytes(woff2_fixture(extra_tables={b"post": b"review " + excluded}))
    assert guard._scan_woff_bytes(woff_fixture(extra_tables={b"ZZZZ": b"opaque"})) == {
        "unsupported-compressed-container"
    }
    assert guard._scan_woff2_bytes(woff2_fixture(extra_tables={b"ZZZZ": b"opaque"})) == {
        "unsupported-compressed-container"
    }
    assert guard._scan_woff_bytes(woff_fixture(private=b"opaque")) == {"unsupported-compressed-container"}
    assert guard._scan_woff2_bytes(woff2_fixture(private=b"opaque")) == {"unsupported-compressed-container"}


def test_font_guard_rejects_checksums_brotli_padding_suffix_and_xml_corruption(tmp_path):
    guard = load_guard()

    bad_checksum = bytearray(woff_fixture())
    bad_checksum[60] ^= 1
    assert guard._scan_woff_bytes(bytes(bad_checksum)) == {"malformed-compressed-artifact"}

    malformed_xml = woff_fixture(metadata=b"<metadata>")
    entity_xml = woff2_fixture(metadata=b'<!DOCTYPE metadata [<!ENTITY x "x">]><metadata>&x;</metadata>')
    assert guard._scan_woff_bytes(malformed_xml) == {"malformed-compressed-metadata"}
    assert guard._scan_woff2_bytes(entity_xml) == {"malformed-compressed-metadata"}

    bad_language_record = struct.pack(">HHH", 1, 0, 12) + struct.pack(">H", 1) + struct.pack(">HH", 2, 99) + b"ok"
    assert guard._scan_sfnt_name_table(bad_language_record) == {"malformed-compressed-artifact"}

    bad_brotli = bytearray(woff2_fixture())
    compressed_start = 49
    _name_length, compressed_start, error = guard._woff2_uint_base128(bad_brotli, compressed_start)
    assert error is None
    bad_brotli[compressed_start] ^= 0xFF
    assert "malformed-compressed-artifact" in guard._scan_woff2_bytes(bytes(bad_brotli))

    padded = bytearray(woff2_fixture("ordinary public font with padding"))
    compressed_size = struct.unpack(">I", padded[20:24])[0]
    compressed_start = 49
    _name_length, compressed_start, error = guard._woff2_uint_base128(padded, compressed_start)
    assert error is None
    compressed_end = compressed_start + compressed_size
    assert compressed_end < ((compressed_end + 3) & ~3)
    padded[compressed_end] = 1
    assert guard._scan_woff2_bytes(bytes(padded)) == {"malformed-compressed-artifact"}

    (tmp_path / "wrong.woff").write_bytes(woff2_fixture())
    (tmp_path / "wrong.woff2").write_bytes(woff_fixture())
    assert {(finding.path, finding.reason) for finding in guard.scan_root(tmp_path)} == {
        ("wrong.woff", "malformed-compressed-artifact"),
        ("wrong.woff2", "malformed-compressed-artifact"),
    }


def test_font_guard_enforces_declared_bounds_and_fails_closed_without_brotli(monkeypatch):
    guard = load_guard()
    woff = woff_fixture()
    woff2 = woff2_fixture()

    monkeypatch.setattr(guard, "FONT_MAX_TABLE_BYTES", 8)
    assert guard._scan_woff_bytes(woff) == {"compressed-artifact-limit"}
    assert guard._scan_woff2_bytes(woff2) == {"compressed-artifact-limit"}

    guard = load_guard()
    monkeypatch.setattr(guard, "_BROTLI_DECODER", None)
    monkeypatch.setattr(guard, "_BROTLI_DECODER_ATTEMPTED", True)
    assert guard._scan_woff2_bytes(woff2) == {"unsupported-compressed-container"}


def test_guard_scans_concatenated_png_zlib_and_rejects_unused_garbage(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    concatenated = zlib.compress(b"ordinary ") + zlib.compress(excluded.encode())
    valid = guard.PNG_SIGNATURE + png_chunk(b"zTXt", b"Comment\x00\x00" + concatenated) + png_chunk(b"IEND", b"")
    invalid = (
        guard.PNG_SIGNATURE
        + png_chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"ordinary") + b"garbage")
        + png_chunk(b"IEND", b"")
    )
    (tmp_path / "concatenated.png").write_bytes(valid)
    (tmp_path / "unused.png").write_bytes(invalid)

    findings = guard.scan_root(tmp_path)

    assert ("concatenated.png", "content") in {(item.path, item.reason) for item in findings}
    assert ("unused.png", "malformed-compressed-metadata") in {(item.path, item.reason) for item in findings}


def test_guard_scans_pdf_flate_and_concatenated_gzip_and_rejects_unknown_containers(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    (tmp_path / "proof.pdf").write_bytes(pdf_with_flate_stream(f"visible {excluded}".encode()))
    (tmp_path / "proof.txt.gz").write_bytes(gzip.compress(b"ordinary ") + gzip.compress(excluded.encode()))
    (tmp_path / "broken.gz").write_bytes(guard.GZIP_SIGNATURE + b"broken")
    (tmp_path / "archive.zip").write_bytes(b"PK\x03\x04opaque")

    findings = guard.scan_root(tmp_path)
    by_path = {(item.path, item.reason) for item in findings}

    assert ("proof.pdf", "content") in by_path
    assert ("proof.txt.gz", "content") in by_path
    assert ("broken.gz", "malformed-compressed-artifact") in by_path
    assert ("archive.zip", "unsupported-compressed-container") in by_path


def test_gzip_scans_every_member_header_field_and_validates_each_header():
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS)).encode()
    archive = gzip_member(b"ordinary first body", extra=b"EX\x00 " + excluded, header_crc=True) + gzip_member(
        b"ordinary second body",
        filename=b"public-name.txt",
        comment=b"ratio=NaN",
        header_crc=True,
    )
    reasons = guard._scan_gzip_bytes(archive)

    assert "content" in reasons
    assert "invalid-rendered-value" in reasons

    bad_second_header = gzip_member(b"first") + bytes([0x1F, 0x8B, 8, 0x20]) + b"broken"
    assert "malformed-compressed-artifact" in guard._scan_gzip_bytes(bad_second_header)
    bad_crc = bytearray(gzip_member(b"body", header_crc=True))
    bad_crc[10] ^= 1
    assert "malformed-compressed-artifact" in guard._scan_gzip_bytes(bytes(bad_crc))


def test_gzip_header_metadata_bounds_and_truncation_fail_closed(monkeypatch):
    guard = load_guard()
    monkeypatch.setattr(guard, "GZIP_MAX_HEADER_METADATA_BYTES", 8)
    unterminated_name = b"\x1f\x8b\x08\x08\x00\x00\x00\x00\x00\xff" + b"x" * 32
    truncated_extra = b"\x1f\x8b\x08\x04\x00\x00\x00\x00\x00\xff\x10\x00tiny"

    assert guard._scan_gzip_bytes(unterminated_name) == {"compressed-artifact-limit"}
    assert guard._scan_gzip_bytes(truncated_extra) == {"compressed-artifact-limit"}
    monkeypatch.setattr(guard, "GZIP_MAX_HEADER_METADATA_BYTES", 1024)
    assert guard._scan_gzip_bytes(truncated_extra) == {"malformed-compressed-artifact"}


def test_pdf_parses_long_balanced_dictionaries_and_rejects_unmatched_or_over_bound_streams(monkeypatch):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS)).encode()
    long_dictionary = pdf_with_flate_stream_and_dictionary_padding(
        b"visible " + excluded,
        b" ".join(f"/Pad{index} (ordinary)".encode() for index in range(700)),
    )
    assert len(long_dictionary.split(b"stream", 1)[0]) > 8192
    assert "content" in guard._scan_pdf_bytes(long_dictionary)

    unmatched = b"%PDF-1.4\n1 0 obj\n<< /Length 4 >>\nstream\ndata\n%%EOF\n"
    assert "malformed-compressed-artifact" in guard._scan_pdf_bytes(unmatched)

    monkeypatch.setattr(guard, "PDF_MAX_DICTIONARY_BYTES", 64)
    over_bound = pdf_with_flate_stream_and_dictionary_padding(b"ordinary", b"/Pad (ordinary) " * 20)
    assert "compressed-artifact-limit" in guard._scan_pdf_bytes(over_bound)


def test_pdf_length_uses_decoded_top_level_name_and_ignores_syntactic_impostors():
    guard = load_guard()
    dictionary = (
        b"<< % /Length 999\r"
        b"/#4cength % a comment may separate a key and value\r\n"
        b"4 /Text (/Length 999) /Hex <2f4c656e67746820393939> "
        b"/FilterText (/Filter /Unknown) "
        b"/Nested << /Length 999 /Filter /Unknown /Subtype /Image >> >>"
    )

    assert guard._pdf_direct_stream_length(dictionary) == (4, None)
    assert guard._scan_pdf_bytes(pdf_with_stream_dictionary(b"data", dictionary)) == set()

    duplicate_alias = b"<< /Length 4 /#4cength 4 >>"
    assert guard._pdf_direct_stream_length(duplicate_alias) == (None, "unsupported-compressed-container")

    duplicate_generic_alias = b"<< /Pad null /#50ad null /Length 4 >>"
    assert guard._pdf_direct_stream_length(duplicate_generic_alias) == (None, "malformed-compressed-artifact")

    nested_only = b"<< /Text (/Length 4) /Nested << /Length 4 >> >>"
    assert guard._pdf_direct_stream_length(nested_only) == (None, "malformed-compressed-artifact")

    compressed = zlib.compress(b"ordinary")
    escaped_filter = b"<< /Length " + str(len(compressed)).encode() + b" /#46ilter /#46lateDecode /#53ubtype /Text >>"
    assert guard._scan_pdf_bytes(pdf_with_stream_dictionary(compressed, escaped_filter)) == set()


def test_pdf_filter_array_is_applied_in_order_and_direct_name_is_equivalent():
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS)).encode()
    payload = b"visible " + excluded
    once = zlib.compress(payload)
    twice = zlib.compress(once)
    direct = b"<< /Length " + str(len(once)).encode() + b" /Filter /FlateDecode >>"
    array_once = b"<< /Length " + str(len(once)).encode() + b" /Filter [/FlateDecode] >>"
    chained = b"<< /Length " + str(len(twice)).encode() + b" /Filter [/FlateDecode /FlateDecode] >>"

    assert "content" in guard._scan_pdf_bytes(pdf_with_stream_dictionary(once, direct))
    assert "content" in guard._scan_pdf_bytes(pdf_with_stream_dictionary(once, array_once))
    assert "content" in guard._scan_pdf_bytes(pdf_with_stream_dictionary(twice, chained))


@pytest.mark.parametrize(
    ("filter_entry", "expected_reason"),
    [
        (b"/Filter []", "malformed-compressed-artifact"),
        (b"/Filter [/FlateDecode null]", "malformed-compressed-artifact"),
        (b"/Filter /UnknownDecode", "unsupported-compressed-container"),
        (b"/Filter /UnknownAbbreviation", "unsupported-compressed-container"),
        (b"/Filter /FlateDecode /DecodeParms << /Predictor 12 >>", "unsupported-compressed-container"),
        (b"/Filter /FlateDecode /DP null", "unsupported-compressed-container"),
        (b"/F /FlateDecode", "unsupported-compressed-container"),
        (b"/Filter /FlateDecode /FFilter /FlateDecode", "unsupported-compressed-container"),
        (b"/Filter /FlateDecode /FDecodeParms null", "unsupported-compressed-container"),
    ],
)
def test_pdf_filter_and_decode_parameter_forms_fail_closed(filter_entry, expected_reason):
    guard = load_guard()
    stream = zlib.compress(b"ordinary")
    dictionary = b"<< /Length " + str(len(stream)).encode() + b" " + filter_entry + b" >>"

    assert expected_reason in guard._scan_pdf_bytes(pdf_with_stream_dictionary(stream, dictionary))


def test_pdf_filter_duplicate_and_overlong_arrays_fail_closed(monkeypatch):
    guard = load_guard()
    stream = zlib.compress(b"ordinary")
    duplicate = b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode /#46ilter /FlateDecode >>"
    assert guard._scan_pdf_bytes(pdf_with_stream_dictionary(stream, duplicate)) == {"malformed-compressed-artifact"}

    monkeypatch.setattr(guard, "PDF_MAX_FILTERS", 2)
    overlong = b"<< /Length " + str(len(stream)).encode() + b" /Filter [/FlateDecode /FlateDecode /FlateDecode] >>"
    assert guard._scan_pdf_bytes(pdf_with_stream_dictionary(stream, overlong)) == {"compressed-artifact-limit"}


def test_pdf_flate_stages_enforce_member_byte_ratio_and_recursion_bounds(monkeypatch):
    guard = load_guard()
    concatenated = zlib.compress(b"one") + zlib.compress(b"two")
    dictionary = b"<< /Length " + str(len(concatenated)).encode() + b" /Filter /FlateDecode >>"
    assert guard._scan_pdf_bytes(pdf_with_stream_dictionary(concatenated, dictionary)) == {"compressed-artifact-limit"}

    expanded = zlib.compress(b"x" * 100)
    expanded_dictionary = b"<< /Length " + str(len(expanded)).encode() + b" /Filter /FlateDecode >>"
    monkeypatch.setattr(guard, "DECOMPRESSED_ARTIFACT_MAX_BYTES", 32)
    assert guard._scan_pdf_bytes(pdf_with_stream_dictionary(expanded, expanded_dictionary)) == {
        "compressed-artifact-limit"
    }

    guard = load_guard()
    monkeypatch.setattr(guard, "PDF_MAX_FILTER_DECODE_RATIO", 1)
    assert guard._scan_pdf_bytes(pdf_with_stream_dictionary(expanded, expanded_dictionary)) == {
        "compressed-artifact-limit"
    }

    guard = load_guard()
    nested = pdf_with_stream_dictionary(b"data", b"<< /Length 4 >>")
    encoded_nested = zlib.compress(nested)
    nested_dictionary = b"<< /Length " + str(len(encoded_nested)).encode() + b" /Filter /FlateDecode >>"
    monkeypatch.setattr(guard, "PDF_MAX_FILTER_RECURSION", 0)
    assert guard._scan_pdf_bytes(pdf_with_stream_dictionary(encoded_nested, nested_dictionary)) == {
        "compressed-artifact-limit"
    }


def test_pdf_dct_image_and_supported_pre_filters_scan_jpeg_metadata():
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS)).encode()
    comment = b"visible " + excluded
    jpeg = b"\xff\xd8\xff\xfe" + struct.pack(">H", len(comment) + 2) + comment + b"\xff\xd9"
    direct = b"<< /Length " + str(len(jpeg)).encode() + b" /Subtype /Image /Filter /DCTDecode >>"
    abbreviated = b"<< /Length " + str(len(jpeg)).encode() + b" /Subtype /Image /Filter /DCT >>"
    compressed = zlib.compress(jpeg)
    chained = b"<< /Length " + str(len(compressed)).encode() + b" /Subtype /Image /Filter [/FlateDecode /DCTDecode] >>"
    routed = b"<< /Length " + str(len(compressed)).encode() + b" /Subtype /Image /Filter /FlateDecode >>"

    assert "content" in guard._scan_pdf_bytes(pdf_with_stream_dictionary(jpeg, direct))
    assert "content" in guard._scan_pdf_bytes(pdf_with_stream_dictionary(jpeg, abbreviated))
    assert "content" in guard._scan_pdf_bytes(pdf_with_stream_dictionary(compressed, chained))
    assert "content" in guard._scan_pdf_bytes(pdf_with_stream_dictionary(compressed, routed))


def test_post_sos_jpeg_comment_is_pillow_valid_and_scanned_directly_and_through_pdf():
    image_module = pytest.importorskip("PIL.Image")
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS)).encode()
    buffer = io.BytesIO()
    image_module.new("RGB", (16, 16), color=(21, 84, 42)).save(buffer, format="JPEG", progressive=True)
    encoded = buffer.getvalue()
    assert encoded.count(b"\xff\xda") > 1
    eoi = encoded.rfind(b"\xff\xd9")
    assert eoi == len(encoded) - 2
    comment = b"public review " + excluded
    jpeg = encoded[:eoi] + b"\xff\xfe" + struct.pack(">H", len(comment) + 2) + comment + encoded[eoi:]
    image_module.open(io.BytesIO(jpeg)).verify()

    direct_dictionary = b"<< /Length " + str(len(jpeg)).encode() + b" /Subtype /Image /Filter /DCTDecode >>"
    compressed = zlib.compress(jpeg)
    chained_dictionary = (
        b"<< /Length " + str(len(compressed)).encode() + b" /Subtype /Image /Filter [/FlateDecode /DCTDecode] >>"
    )

    assert "content" in guard._scan_jpeg_bytes(jpeg)
    assert "content" in guard._scan_pdf_bytes(pdf_with_stream_dictionary(jpeg, direct_dictionary))
    assert "content" in guard._scan_pdf_bytes(pdf_with_stream_dictionary(compressed, chained_dictionary))


def test_jpeg_entropy_stuffing_fill_restarts_and_multiple_scans_resume_metadata_parsing():
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS)).encode()
    scan_header = b"\xff\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
    comment = b"review " + excluded
    jpeg = (
        b"\xff\xd8"
        + scan_header
        + b"\x11\xff\x00\x22\xff\xff\xd0\x33"
        + scan_header
        + b"\x44\xff\x00\x55"
        + b"\xff\xff\xfe"
        + struct.pack(">H", len(comment) + 2)
        + comment
        + b"\xff\xff\xd9"
    )

    assert guard._scan_jpeg_bytes(jpeg) == {"content"}


@pytest.mark.parametrize(
    "jpeg",
    [
        b"\xff\xd8\xff\xd9trailing",
        b"\xff\xd8\xff\xda\x00\x02unterminated",
        b"\xff\xd8\xff\xda\x00\x02\x11\xff\xff\x00\x22\xff\xd9",
        b"\xff\xd8\xff\xfe\x00\x02not-a-marker",
        b"\xff\xd8\xff\xd8\xff\xd9",
    ],
)
def test_jpeg_rejects_trailing_unterminated_bad_stuffing_and_malformed_marker_forms(jpeg):
    guard = load_guard()

    assert "malformed-compressed-artifact" in guard._scan_jpeg_bytes(jpeg)


@pytest.mark.parametrize("reserved_marker", [0x02, 0x40, 0xBF])
def test_jpeg_reserved_markers_after_entropy_fail_direct_and_pdf_filter_paths(reserved_marker: int):
    guard = load_guard()
    jpeg = b"\xff\xd8\xff\xda\x00\x02\x11\xff" + bytes([reserved_marker]) + b"\x00\x02\xff\xd9"
    direct_dictionary = b"<< /Length " + str(len(jpeg)).encode() + b" /Subtype /Image /Filter /DCTDecode >>"
    compressed = zlib.compress(jpeg)
    chained_dictionary = (
        b"<< /Length " + str(len(compressed)).encode() + b" /Subtype /Image /Filter [/FlateDecode /DCTDecode] >>"
    )

    assert guard._scan_jpeg_bytes(jpeg) == {"malformed-compressed-artifact"}
    assert guard._scan_pdf_bytes(pdf_with_stream_dictionary(jpeg, direct_dictionary)) == {
        "malformed-compressed-artifact"
    }
    assert guard._scan_pdf_bytes(pdf_with_stream_dictionary(compressed, chained_dictionary)) == {
        "malformed-compressed-artifact"
    }


@pytest.mark.parametrize(
    "dictionary_suffix",
    [
        b"/Subtype /Image /Filter /UnknownDecode",
        b"/Subtype /Image /Filter /DCTx",
        b"/Subtype /Image /Filter [/DCTDecode /FlateDecode]",
        b"/Filter /DCTDecode",
    ],
)
def test_pdf_image_unknown_filters_and_unsupported_dct_combinations_fail_closed(dictionary_suffix):
    guard = load_guard()
    jpeg = b"\xff\xd8\xff\xd9"
    dictionary = b"<< /Length " + str(len(jpeg)).encode() + b" " + dictionary_suffix + b" >>"

    assert guard._scan_pdf_bytes(pdf_with_stream_dictionary(jpeg, dictionary)) == {"unsupported-compressed-container"}


@pytest.mark.parametrize(
    "value",
    [b"4 0 R", b"(4)", b"<34>", b"[4]", b"-1", b"4.0"],
)
def test_pdf_length_rejects_non_direct_nonnegative_integer_objects(value):
    guard = load_guard()
    dictionary = b"<< /Length " + value + b" >>"

    assert guard._pdf_direct_stream_length(dictionary) == (None, "unsupported-compressed-container")
    assert "unsupported-compressed-container" in guard._scan_pdf_bytes(pdf_with_stream_dictionary(b"data", dictionary))


@pytest.mark.parametrize(
    "dictionary",
    [
        b"<< /#4gength 4 >>",
        b"<< /Length 4garbage >>",
        b"<< /Length 4 } >>",
        b"<< /Length 4 /MissingValue >>",
        b"<< /Length 4 /Unmatched [null >>",
    ],
)
def test_pdf_length_rejects_malformed_tokens(dictionary):
    guard = load_guard()

    assert guard._pdf_direct_stream_length(dictionary) == (None, "malformed-compressed-artifact")


def test_pdf_length_numeric_and_tokenizer_limits_fail_closed(monkeypatch):
    guard = load_guard()
    overflow = b"<< /Length 999999999999999999999999999999999999 >>"
    assert guard._pdf_direct_stream_length(overflow) == (None, "compressed-artifact-limit")

    monkeypatch.setattr(guard, "PDF_MAX_DICTIONARY_TOKENS", 4)
    exhausted = b"<< /Pad null /Length 4 >>"
    assert guard._pdf_direct_stream_length(exhausted) == (None, "compressed-artifact-limit")
    assert "compressed-artifact-limit" in guard._scan_pdf_bytes(pdf_with_stream_dictionary(b"data", exhausted))


@pytest.mark.parametrize("stream_eol", [b"\n", b"\r\n", b"\r"])
def test_pdf_stream_keyword_accepts_only_supported_eol_forms(stream_eol):
    guard = load_guard()
    pdf = b"%PDF-1.4\n1 0 obj\n<< /Length 4 >>\nstream" + stream_eol + b"data\nendstream\nendobj\n%%EOF\n"

    assert guard._scan_pdf_bytes(pdf) == set()


@pytest.mark.parametrize("invalid_separator", [b" ", b"\t", b"\x0c"])
def test_pdf_stream_keyword_rejects_missing_or_invalid_eol(invalid_separator):
    guard = load_guard()
    pdf = b"%PDF-1.4\n1 0 obj\n<< /Length 4 >>\nstream" + invalid_separator + b"data\nendstream\nendobj\n%%EOF\n"

    assert guard._scan_pdf_bytes(pdf) == {"malformed-compressed-artifact"}


def test_pdf_stream_words_inside_literal_name_and_comment_are_not_stream_markers():
    guard = load_guard()
    assert not guard._pdf_gap_is_trivia(b" % stream")
    pdf = b"%PDF-1.4\n1 0 obj\n<< /Length 4 >> % stream is comment text\rstream\rdata\nendstream\nendobj\n%%EOF\n"
    prefixed = (
        b"%PDF-1.4\n1 0 obj\n<< /Text (stream\n) /Hex <73747265616d> /Name /stream >>\nendobj\n"
        b"2 0 obj\n<< /Length 4 >>\nstream\ndata\nendstream\nendobj\n%%EOF\n"
    )

    assert guard._scan_pdf_bytes(pdf) == set()
    assert guard._scan_pdf_bytes(prefixed) == set()

    no_dictionary = b"%PDF-1.4\nstream\ndata\nendstream\n%%EOF\n"
    assert guard._scan_pdf_bytes(no_dictionary) == {"malformed-compressed-artifact"}


@pytest.mark.parametrize(
    "fake_dictionary",
    [
        b"% << /Length 4 >>\n",
        b"(<< /Length 4 >>)\n",
        b"<3c3c202f4c656e6774682034203e3e>\n",
        b"/Fake#3c#3c#2fLength#2034#3e#3e\n",
    ],
)
def test_pdf_dictionary_starts_inside_comment_literal_hex_and_name_are_not_syntax(fake_dictionary):
    guard = load_guard()
    pdf = b"%PDF-1.4\n" + fake_dictionary + b"stream\ndata\nendstream\n%%EOF\n"

    assert guard._scan_pdf_bytes(pdf) == {"malformed-compressed-artifact"}


def test_pdf_endstream_must_be_the_delimited_keyword_at_the_declared_boundary():
    guard = load_guard()
    prefixed_keyword = b"%PDF-1.4\n<< /Length 4 >>\nstream\ndata\n/endstream\n%%EOF\n"
    suffixed_keyword = b"%PDF-1.4\n<< /Length 4 >>\nstream\ndata\nendstreamSuffix\n%%EOF\n"

    assert guard._scan_pdf_bytes(prefixed_keyword) == {"malformed-compressed-artifact"}
    assert guard._scan_pdf_bytes(suffixed_keyword) == {"malformed-compressed-artifact"}


@pytest.mark.parametrize("separator", [b"\r", b"\n", b"\r\n"])
def test_pdf_endstream_requires_and_accepts_only_a_real_eol_after_declared_data(separator):
    guard = load_guard()
    pdf = b"%PDF-1.4\n<< /Length 4 >>\nstream\ndata" + separator + b"endstream\n%%EOF\n"

    assert guard._scan_pdf_bytes(pdf) == set()


@pytest.mark.parametrize("payload", [b"abc ", b"abc)"])
def test_pdf_endstream_rejects_immediate_keyword_after_delimiter_ending_payload(payload):
    guard = load_guard()
    pdf = b"%PDF-1.4\n<< /Length 4 >>\nstream\n" + payload + b"endstream\n%%EOF\n"

    assert guard._scan_pdf_bytes(pdf) == {"malformed-compressed-artifact"}


def test_pdf_lexical_stream_walk_has_an_explicit_token_bound(monkeypatch):
    guard = load_guard()
    monkeypatch.setattr(guard, "PDF_MAX_LEXICAL_TOKENS", 4)

    assert guard._scan_pdf_bytes(pdf_with_stream_dictionary(b"data", b"<< /Length 4 >>")) == {
        "compressed-artifact-limit"
    }


def test_gzip_member_and_output_bounds_fail_closed(monkeypatch):
    guard = load_guard()
    monkeypatch.setattr(guard, "COMPRESSED_MAX_MEMBERS", 2)
    too_many = gzip.compress(b"one") + gzip.compress(b"two") + gzip.compress(b"three")
    assert guard._scan_gzip_bytes(too_many) == {"compressed-artifact-limit"}

    monkeypatch.setattr(guard, "DECOMPRESSED_ARTIFACT_MAX_BYTES", 8)
    bomb = gzip.compress(b"ordinary public payload")
    assert guard._scan_gzip_bytes(bomb) == {"compressed-artifact-limit"}


def test_decoder_bounds_fail_closed_for_size_round_representation_and_base64_limits(tmp_path, monkeypatch):
    guard = load_guard()
    oversized = "A" * (policy.PUBLIC_DECODE_MAX_BASE64_TOKEN_CHARS + 1)
    tokens = [base64.b64encode(f"public value {index:03d}".encode()).decode() for index in range(65)]
    nested = "ordinary public value"
    for _ in range(policy.PUBLIC_DECODE_MAX_ROUNDS + 2):
        nested = base64.b64encode(nested.encode()).decode()
    (tmp_path / "oversized.txt").write_text(oversized, encoding="utf-8")
    (tmp_path / "too-many.txt").write_text(" ".join(tokens), encoding="utf-8")
    (tmp_path / "too-deep.txt").write_text(nested, encoding="utf-8")
    (tmp_path / "oversized-data-uri.json").write_text(
        '"data:image/png;base64,' + "A" * (guard.DATA_URI_MAX_ENCODED_CHARS + 1) + '"',
        encoding="utf-8",
    )

    findings = guard.scan_root(tmp_path)

    assert {item.path for item in findings if item.reason == "decode-limit"} == {
        "oversized-data-uri.json",
        "oversized.txt",
        "too-deep.txt",
        "too-many.txt",
    }
    assert policy.decode_public_text("x" * (policy.PUBLIC_DECODE_MAX_INPUT_CHARS + 1)).limit_hit
    monkeypatch.setattr(policy, "PUBLIC_DECODE_MAX_VARIANTS_PER_WINDOW", 1)
    assert policy.decode_public_text("%25%36%31").limit_hit
    monkeypatch.setattr(policy, "PUBLIC_DECODE_MAX_BASE64_RESULT_CHARS", 2)
    assert policy.decode_public_text(base64.b64encode(b"ordinary").decode()).limit_hit


def test_guard_has_linear_zero_and_png_fast_paths(tmp_path, monkeypatch):
    guard = load_guard()
    zero_size = 8 * 1024 * 1024
    (tmp_path / "zeros.bin").write_bytes(b"\x00" * zero_size)
    idat = b"\x00" * (8 * 1024 * 1024)
    png = guard.PNG_SIGNATURE + png_chunk(b"IDAT", idat) + png_chunk(b"IEND", b"")
    (tmp_path / "pixels.png").write_bytes(png)
    calls = 0
    original = guard._value_reasons

    def counted(value):
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(guard, "_value_reasons", counted)
    started = time.perf_counter()
    findings = guard.scan_root(tmp_path)
    elapsed = time.perf_counter() - started

    assert findings == []
    assert calls <= zero_size // guard.BINARY_CHUNK_SIZE + 3
    assert elapsed < 5.0


def test_guard_rejects_hardlinks_without_scanning_shared_inode(tmp_path):
    guard = load_guard()
    source = tmp_path / "source.txt"
    source.write_text("ordinary", encoding="utf-8")
    os.link(source, tmp_path / "alias.txt")

    findings = guard.scan_root(tmp_path)

    assert {(item.path, item.reason) for item in findings} == {
        ("alias.txt", "hardlink"),
        ("source.txt", "hardlink"),
    }


def test_guard_scans_utf16_binary_metadata_with_and_without_boms(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    metadata = f"artist={excluded}"
    fixtures = {
        "utf16le-bom.bin": b"\xff\xfe" + metadata.encode("utf-16-le"),
        "utf16be-bom.bin": b"\xfe\xff" + metadata.encode("utf-16-be"),
        "utf16le-no-bom.bin": metadata.encode("utf-16-le"),
        "utf16be-no-bom.bin": metadata.encode("utf-16-be"),
        "utf16le-no-bom.json": ('{"artist":"' + excluded + '"}').encode("utf-16-le"),
    }
    for name, payload in fixtures.items():
        (tmp_path / name).write_bytes(payload)

    findings = guard.scan_root(tmp_path)

    content_paths = {finding.path for finding in findings if finding.reason == "content"}
    assert content_paths == set(fixtures)
    assert excluded not in str(findings).casefold()


def test_guard_decodes_entities_unicode_percent_and_encoded_filenames(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    entity = "".join(f"&#{ord(char)};" for char in excluded)
    unicode_escaped = "".join(f"\\u{ord(char):04x}" for char in excluded)
    percent_encoded = "".join(f"%{byte:02X}" for byte in excluded.encode())
    base64_encoded = base64.b64encode(excluded.encode()).decode()

    (tmp_path / "entity.html").write_text(entity, encoding="utf-8")
    (tmp_path / "unicode.json").write_text(json.dumps({"name": unicode_escaped}), encoding="utf-8")
    (tmp_path / "percent.txt").write_text(percent_encoded, encoding="utf-8")
    (tmp_path / "base64.txt").write_text(base64_encoded, encoding="utf-8")
    (tmp_path / f"{percent_encoded}.html").write_text("clean", encoding="utf-8")

    findings = guard.scan_root(tmp_path)

    assert {finding.reason for finding in findings} == {"content", "filename"}
    assert {finding.route for finding in findings} == {
        "/entity",
        "/base64.txt",
        "/percent.txt",
        f"/{policy.PUBLIC_CROP_REDACTION}",
        "/unicode.json",
    }
    assert excluded not in str(findings).casefold()


def test_guard_allows_valid_css_none_values_but_rejects_nonfinite_css_values(tmp_path):
    guard = load_guard()
    (tmp_path / "valid.css").write_text(
        ".hidden{display:none}.plain{background:none;border:none;animation:none;transform:none;filter:none}\n",
        encoding="utf-8",
    )
    (tmp_path / "invalid.css").write_text(".broken{opacity:NaN;width:.inf%}\n", encoding="utf-8")

    findings = guard.scan_root(tmp_path)

    assert {(finding.path, finding.reason) for finding in findings} == {("invalid.css", "invalid-rendered-value")}


def test_guard_rejects_encoded_and_binary_currency_sentinels_but_allows_json_null(tmp_path):
    guard = load_guard()
    (tmp_path / "entity.html").write_text("USD&nbsp;NaN", encoding="utf-8")
    (tmp_path / "unicode.json").write_text(r'{"cost":"\u0055\u0053\u0044\u0020inf"}', encoding="utf-8")
    (tmp_path / "%24None.html").write_text("clean", encoding="utf-8")
    (tmp_path / "metadata.bin").write_bytes(b"\x00metadata=USD nan\x00")
    (tmp_path / "raw-string.json").write_text('{"cost": "None"}', encoding="utf-8")
    (tmp_path / "valid.json").write_text('{"cost": null, "description": "none available"}', encoding="utf-8")

    findings = guard.scan_root(tmp_path)

    assert {finding.path for finding in findings} == {
        "entity.html",
        "invalid-rendered-value.html",
        "metadata.bin",
        "raw-string.json",
        "unicode.json",
    }
    assert {finding.reason for finding in findings} == {"invalid-rendered-value"}
    assert all(finding.path != "valid.json" for finding in findings)


def test_guard_rejects_canonical_nonfinite_values_without_flagging_lookalikes(tmp_path):
    guard = load_guard()
    invalid = {
        "yaml-nan.yaml": "score: .nan\n",
        "yaml-inf.yml": "upper_bound: -.inf\n",
        "currency.txt": "Projected cost: USD .nan\n",
        "percentage.html": "<span>NaN%</span>\n",
        "csv-value.csv": "metric,value\nvpd,Infinity\n",
        "json-value.json": '{"ratio":"+inf"}\n',
    }
    for name, content in invalid.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    (tmp_path / "metadata.bin").write_bytes("ratio=.inf".encode("utf-16-be"))
    (tmp_path / "NaN%.asset").write_bytes(b"clean")
    (tmp_path / "safe-lookalikes.txt").write_text(
        "banana nan_value infographic infinite infinity-pool USD infinity-pool cost=nan_value .info none available\n",
        encoding="utf-8",
    )
    (tmp_path / "safe-null.json").write_text('{"ratio": null}\n', encoding="utf-8")

    findings = guard.scan_root(tmp_path)

    invalid_paths = {finding.path for finding in findings if finding.reason == "invalid-rendered-value"}
    assert invalid_paths == {*invalid, "metadata.bin", "invalid-rendered-value.asset"}
    assert all(finding.path != "NaN%.asset" for finding in findings)
    assert all(finding.path not in {"safe-lookalikes.txt", "safe-null.json"} for finding in findings)


def test_guard_sanitizes_roots_and_missing_root_errors_deterministically(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    encoded = "".join(f"%{byte:02X}" for byte in excluded.encode())
    root = tmp_path / encoded
    root.mkdir()
    (root / "index.md").write_text(f"{excluded}\n", encoding="utf-8")

    findings = guard.scan_root(root)
    first = guard.report_payload([root], findings)
    second = guard.report_payload([root], findings)

    assert first == second
    assert excluded not in json.dumps(first).casefold()
    assert encoded.casefold() not in json.dumps(first).casefold()
    finding_proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "check-public-output.py"),
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert finding_proc.returncode == 1
    assert excluded not in finding_proc.stderr.casefold()
    assert encoded.casefold() not in finding_proc.stderr.casefold()

    missing = root / "missing"
    missing_report = tmp_path / "missing-report.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "check-public-output.py"),
            "--root",
            str(missing),
            "--json-report",
            str(missing_report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert excluded not in proc.stderr.casefold()
    assert encoded.casefold() not in proc.stderr.casefold()
    missing_payload = json.loads(missing_report.read_text(encoding="utf-8"))
    assert missing_payload["missing_roots"]
    assert excluded not in json.dumps(missing_payload).casefold()


def test_guard_report_write_failure_is_generic_and_non_reflective(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    (root / "index.md").write_text("ordinary", encoding="utf-8")
    report_directory = tmp_path / "private-report-name"
    report_directory.mkdir()

    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "check-public-output.py"),
            "--root",
            str(root),
            "--json-report",
            str(report_directory),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 2
    assert proc.stderr.strip() == "public-output guard: report write failed"
    assert report_directory.name not in proc.stderr


def test_guard_rejects_file_and_directory_symlinks_without_following_targets(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    root = tmp_path / "public"
    root.mkdir()
    same_root_file = root / "real-file.txt"
    same_root_file.write_text("clean\n", encoding="utf-8")
    same_root_dir = root / "real-dir"
    same_root_dir.mkdir()
    (same_root_dir / "index.md").write_text("clean\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "private.txt"
    outside_file.write_text(f"do not read {excluded}\n", encoding="utf-8")
    outside_dir = outside / "private-dir"
    outside_dir.mkdir()
    (outside_dir / "index.md").write_text(f"do not read {excluded}\n", encoding="utf-8")
    (root / "same-file-link").symlink_to(same_root_file)
    (root / "outside-file-link").symlink_to(outside_file)
    (root / "same-dir-link").symlink_to(same_root_dir, target_is_directory=True)
    (root / "outside-dir-link").symlink_to(outside_dir, target_is_directory=True)

    findings = guard.scan_root(root)
    payload = guard.report_payload([root], findings)

    symlink_paths = {finding.path for finding in findings if finding.reason == "symlink"}
    assert symlink_paths == {
        "outside-dir-link",
        "outside-file-link",
        "same-dir-link",
        "same-file-link",
    }
    assert all(finding.reason != "content" for finding in findings)
    assert excluded not in json.dumps(payload).casefold()


def test_guard_rejects_symlinked_and_traversing_roots_without_reading_targets(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    (real_root / "index.md").write_text(f"do not read {excluded}\n", encoding="utf-8")
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(real_root, target_is_directory=True)

    symlink_findings = guard.scan_root(linked_root)
    traversal_findings = guard.scan_root(tmp_path / "declared" / ".." / "real-root")

    assert [(finding.path, finding.reason) for finding in symlink_findings] == [(".", "symlink")]
    assert [(finding.path, finding.reason) for finding in traversal_findings] == [(".", "path-traversal")]
    payload = guard.report_payload([linked_root], symlink_findings)
    assert excluded not in json.dumps(payload).casefold()
    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "check-public-output.py"),
            "--root",
            str(linked_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "symlink" in proc.stderr
    assert excluded not in proc.stderr.casefold()


def test_guard_rejects_special_entries_and_unreadable_directories_without_opening_them(tmp_path):
    guard = load_guard()
    excluded = next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    root = tmp_path / "public"
    root.mkdir()
    fifo = root / "blocked.pipe"
    os.mkfifo(fifo)
    unreadable = root / "unreadable"
    unreadable.mkdir()
    (unreadable / "private.md").write_text(f"do not read {excluded}\n", encoding="utf-8")
    unreadable.chmod(0)
    try:
        findings = guard.scan_root(root)
    finally:
        unreadable.chmod(0o700)

    by_path = {(finding.path, finding.reason) for finding in findings}
    assert ("blocked.pipe", "special-entry") in by_path
    assert ("unreadable", "unreadable-directory") in by_path
    assert all(finding.reason != "content" for finding in findings)
    assert excluded not in json.dumps(guard.report_payload([root], findings)).casefold()


def test_publish_and_rebuild_scripts_guard_one_same_filesystem_candidate_before_promotion():
    repo_root = Path(__file__).resolve().parents[1]
    publish = (repo_root / "scripts" / "publish-site-content.sh").read_text(encoding="utf-8")
    rebuild = (repo_root / "scripts" / "rebuild-site.sh").read_text(encoding="utf-8")

    assert publish.index("check-public-output.py") < publish.index('if [[ "$REBUILD" == true ]]')
    assert rebuild.count('"$PUBLIC_OUTPUT_GUARD"') == 1
    assert 'mktemp -d "$live_parent/.${live_name}.candidate.XXXXXXXX"' in rebuild
    assert rebuild.index('npx quartz build --output "$staging"') < rebuild.index('"$PUBLIC_OUTPUT_GUARD"')
    assert rebuild.index('"$PUBLIC_OUTPUT_GUARD"') < rebuild.index('--promote-to "$LIVE_PUBLIC"')
    assert "candidate staging rsync" not in rebuild
    assert "VERDIFY_PUBLIC_OUTPUT_GUARD_TIMEOUT:-300" in rebuild
    assert "PUBLIC_OUTPUT_GUARD_TIMEOUT > 600" in rebuild
    assert "atomic-promote-directory.py" in rebuild
    assert '--root "$staging"' in rebuild
    assert '--promote-to "$LIVE_PUBLIC"' in rebuild


def test_rebuild_promotion_is_complete_or_leaves_live_unchanged(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    rebuild = repo_root / "scripts" / "rebuild-site.sh"
    for failure_mode in ("generation", "guard", "success"):
        case = tmp_path / failure_mode
        bin_dir = case / "bin"
        source = case / "source"
        runtime = case / "runtime"
        live = runtime / "public"
        state = case / "state"
        for directory in (bin_dir, source, runtime, live, state):
            directory.mkdir(parents=True, exist_ok=True)
        (live / "index.html").write_text("old-index\n", encoding="utf-8")
        (live / "old-only.html").write_text("old-only\n", encoding="utf-8")
        sleep = bin_dir / "sleep"
        sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        sleep.chmod(0o755)
        npx = bin_dir / "npx"
        npx.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
output=''
while [[ $# -gt 0 ]]; do
  if [[ "$1" == '--output' ]]; then output="$2"; shift 2; else shift; fi
done
mkdir -p "$output"
printf 'new-index\n' > "$output/index.html"
printf 'new-only\n' > "$output/new-only.html"
if [[ "${FIXTURE_REBUILD_FAILURE:-}" == 'generation' ]]; then exit 31; fi
""",
            encoding="utf-8",
        )
        npx.chmod(0o755)
        rsync = bin_dir / "rsync"
        rsync.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
source_dir="${@: -2:1}"
destination="${@: -1}"
mkdir -p "$destination"
if [[ "${FIXTURE_REBUILD_FAILURE:-}" == 'rsync' ]]; then
  printf 'partial\n' > "$destination/index.html"
  exit 32
fi
cp -a "${source_dir%/}/." "${destination%/}/"
""",
            encoding="utf-8",
        )
        rsync.chmod(0o755)
        guard = case / "guard.py"
        guard.write_text(
            "import os\n"
            "import sys\n"
            "if os.environ.get('FIXTURE_REBUILD_FAILURE') == 'guard': raise SystemExit(33)\n"
            f"os.execv(sys.executable, [sys.executable, {str(repo_root / 'scripts' / 'check-public-output.py')!r}, *sys.argv[1:]])\n",
            encoding="utf-8",
        )
        marker = state / "marker"
        env = dict(os.environ)
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "PYTHON": sys.executable,
                "FIXTURE_REBUILD_FAILURE": failure_mode,
                "VERDIFY_SCRIPT_ROOT": str(repo_root / "scripts"),
                "VERDIFY_SITE_SOURCE": str(source),
                "VERDIFY_SITE_RUNTIME": str(runtime),
                "VERDIFY_SITE_PUBLIC": str(live),
                "VERDIFY_SITE_BUILD_ROOT": str(runtime / "builds"),
                "VERDIFY_SITE_BUILD_LOCK": str(state / "build.lock"),
                "VERDIFY_SITE_BUILD_LOG": str(state / "build.log"),
                "VERDIFY_SITE_BUILD_MARKER": str(marker),
                "VERDIFY_SITE_CONTAINER": "",
                "VERDIFY_PUBLIC_OUTPUT_GUARD": str(guard),
                "VERDIFY_PUBLIC_OUTPUT_BUILD_REPORT": str(state / "report.json"),
            }
        )

        proc = subprocess.run(
            ["bash", str(rebuild)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )

        if failure_mode == "success":
            assert proc.returncode == 0, proc.stdout + proc.stderr
            assert (live / "index.html").read_text(encoding="utf-8") == "new-index\n"
            assert (live / "new-only.html").read_text(encoding="utf-8") == "new-only\n"
            assert not (live / "old-only.html").exists()
            assert marker.exists()
        else:
            assert proc.returncode != 0
            assert (live / "index.html").read_text(encoding="utf-8") == "old-index\n"
            assert (live / "old-only.html").read_text(encoding="utf-8") == "old-only\n"
            assert not (live / "new-only.html").exists()
            assert not marker.exists()


def test_atomic_promoter_recovers_aged_sigkill_residue_and_rejects_hardlinks(tmp_path):
    promoter = load_promoter()
    live = tmp_path / "public"
    live.mkdir()
    (live / "index.html").write_text("old", encoding="utf-8")
    candidate = tmp_path / ".public.candidate.Ab12Cd34"
    candidate.mkdir()
    (candidate / "index.html").write_text("new", encoding="utf-8")

    child = os.fork()
    if child == 0:
        promoter.promote(candidate, live, after_exchange=lambda: os.kill(os.getpid(), signal.SIGKILL))
        os._exit(0)
    _pid, status = os.waitpid(child, 0)
    assert os.WIFSIGNALED(status)
    assert os.WTERMSIG(status) == signal.SIGKILL
    assert (live / "index.html").read_text(encoding="utf-8") == "new"
    assert (candidate / "index.html").read_text(encoding="utf-8") == "old"
    old_time = time.time() - 7200
    os.utime(candidate, (old_time, old_time))

    removed = promoter.cleanup_stale_candidates(live, min_age_seconds=3600)

    assert removed == 1
    assert not candidate.exists()
    assert (live / "index.html").read_text(encoding="utf-8") == "new"

    recent = tmp_path / ".public.candidate.Ij90Kl12"
    recent.mkdir()
    (recent / "index.html").write_text("recent", encoding="utf-8")
    assert promoter.cleanup_stale_candidates(live, min_age_seconds=3600) == 0
    assert recent.exists()

    unsafe = tmp_path / ".public.candidate.Ef56Gh78"
    unsafe.mkdir()
    file = unsafe / "index.html"
    file.write_text("unsafe", encoding="utf-8")
    os.link(file, unsafe / "alias.html")
    other_live = tmp_path / "other-public"

    try:
        promoter.promote(unsafe, other_live)
    except ValueError:
        pass
    else:
        raise AssertionError("hardlinked candidate must be rejected")
    assert unsafe.exists()
    assert not other_live.exists()


def test_atomic_exchange_leaves_an_inode_pinned_directory_view_retired_and_empty(tmp_path):
    promoter = load_promoter()
    live = tmp_path / "public"
    live.mkdir()
    (live / "index.html").write_text("release-zero", encoding="utf-8")
    candidate = tmp_path / ".public.candidate.Ab12Cd34"
    candidate.mkdir()
    (candidate / "index.html").write_text("release-one", encoding="utf-8")

    retired_descriptor = os.open(live, os.O_RDONLY | os.O_DIRECTORY)
    try:
        result = promoter.promote(candidate, live)

        assert result.exchanged is True
        assert result.old_live_removed is True
        assert (live / "index.html").read_text(encoding="utf-8") == "release-one"
        # This is the behavior of a Kubernetes subPath bind to the old directory
        # inode: it does not follow the exchanged pathname and cleanup empties it.
        assert os.listdir(retired_descriptor) == []
        with pytest.raises(FileNotFoundError):
            os.open("index.html", os.O_RDONLY, dir_fd=retired_descriptor)
    finally:
        os.close(retired_descriptor)


def test_long_lived_parent_path_reader_observes_two_atomic_publishes_without_restart(tmp_path):
    promoter = load_promoter()
    cache_mount = tmp_path / "cache"
    publisher_root = cache_mount / "publisher"
    live = publisher_root / "public"
    live.mkdir(parents=True)
    (live / "index.html").write_text("release-zero", encoding="utf-8")
    reader_program = """
import pathlib
import sys

root = pathlib.Path(sys.argv[1]) / "publisher" / "public"
for _request in sys.stdin:
    print((root / "index.html").read_text(encoding="utf-8"), flush=True)
"""
    reader = subprocess.Popen(
        [sys.executable, "-c", reader_program, str(cache_mount)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert reader.stdin is not None
    assert reader.stdout is not None
    original_pid = reader.pid

    def request() -> str:
        reader.stdin.write("GET /\n")
        reader.stdin.flush()
        return reader.stdout.readline().strip()

    try:
        assert request() == "release-zero"
        for suffix, release in (("Ab12Cd34", "release-one"), ("Ef56Gh78", "release-two")):
            candidate = publisher_root / f".public.candidate.{suffix}"
            candidate.mkdir()
            (candidate / "index.html").write_text(release, encoding="utf-8")
            result = promoter.promote(candidate, live)
            assert result.old_live_removed is True
            assert reader.poll() is None
            assert reader.pid == original_pid
            assert request() == release
    finally:
        reader.terminate()
        reader.wait(timeout=10)


def test_atomic_promoter_rejects_post_scan_path_swap_and_hardlink_before_exchange(tmp_path):
    promoter = load_promoter()
    live = tmp_path / "public"
    live.mkdir()
    (live / "index.html").write_text("old", encoding="utf-8")

    swapped = tmp_path / ".public.candidate.Ab12Cd34"
    swapped.mkdir()
    (swapped / "index.html").write_text("scanned", encoding="utf-8")
    original = tmp_path / ".public.candidate.original"

    def swap_candidate():
        swapped.rename(original)
        swapped.mkdir()
        (swapped / "index.html").write_text("replacement", encoding="utf-8")
        assert (live / "index.html").read_text(encoding="utf-8") == "old"

    with pytest.raises(ValueError, match="path changed"):
        promoter.promote(swapped, live, before_exchange=swap_candidate)
    assert (live / "index.html").read_text(encoding="utf-8") == "old"
    assert (swapped / "index.html").read_text(encoding="utf-8") == "replacement"

    hardlinked = tmp_path / ".public.candidate.Ef56Gh78"
    hardlinked.mkdir()
    source = hardlinked / "index.html"
    source.write_text("candidate", encoding="utf-8")

    def inject_hardlink():
        os.link(source, hardlinked / "alias.html")
        assert (live / "index.html").read_text(encoding="utf-8") == "old"

    with pytest.raises(ValueError, match="changed after scan|hardlinked"):
        promoter.promote(hardlinked, live, before_exchange=inject_hardlink)
    assert (live / "index.html").read_text(encoding="utf-8") == "old"

    modified = tmp_path / ".public.candidate.Ij90Kl12"
    modified.mkdir()
    modified_file = modified / "index.html"
    modified_file.write_text("candidate", encoding="utf-8")

    with pytest.raises(ValueError, match="changed after scan"):
        promoter.promote(
            modified,
            live,
            before_exchange=lambda: modified_file.write_text("post-scan modification", encoding="utf-8"),
        )
    assert (live / "index.html").read_text(encoding="utf-8") == "old"


def test_guard_and_promoter_share_exact_scanned_descriptor_across_post_scan_swap(tmp_path, monkeypatch):
    guard = load_guard()
    live = tmp_path / "public"
    live.mkdir()
    (live / "index.html").write_text("old", encoding="utf-8")
    candidate = tmp_path / ".public.candidate.Ab12Cd34"
    candidate.mkdir()
    (candidate / "index.html").write_text("scanned", encoding="utf-8")
    original = tmp_path / ".public.candidate.original"
    report = tmp_path / "report.json"
    actual_promote = guard.promote_open_directory

    def race(parent_fd, staged_name, staged_fd, live_name, **kwargs):
        def inject_replacement():
            candidate.rename(original)
            candidate.mkdir()
            (candidate / "index.html").write_text("replacement", encoding="utf-8")
            assert (live / "index.html").read_text(encoding="utf-8") == "old"

        return actual_promote(
            parent_fd,
            staged_name,
            staged_fd,
            live_name,
            before_exchange=inject_replacement,
            **kwargs,
        )

    monkeypatch.setattr(guard, "promote_open_directory", race)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check-public-output.py",
            "--root",
            str(candidate),
            "--json-report",
            str(report),
            "--promote-to",
            str(live),
        ],
    )

    assert guard.main() == 1
    assert (live / "index.html").read_text(encoding="utf-8") == "old"
    assert (candidate / "index.html").read_text(encoding="utf-8") == "replacement"
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["findings"] == []


def test_api_and_publisher_images_copy_and_import_shared_policy():
    repo_root = Path(__file__).resolve().parents[1]
    dockerfiles = [
        (repo_root / "api" / "Dockerfile").read_text(encoding="utf-8"),
        (repo_root / "scripts" / "Dockerfile.lab-publisher").read_text(encoding="utf-8"),
    ]

    for dockerfile in dockerfiles:
        assert "COPY verdify_public/" in dockerfile
        assert "import verdify_public.output_policy" in dockerfile
