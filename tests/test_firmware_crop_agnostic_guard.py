"""L2 #344 AC5 guard — the SHIPPED firmware must contain no crop identity.

Firmware is crop-agnostic by contract (README / docs/firmware-control-contract.md):
it knows sensors, relays, thresholds, setpoints, and numeric bands — never crops.
Crop strategy lives ABOVE firmware (band_defaults.yaml zone->crop map ->
crop_band_anchors -> dispatcher anchor-sync -> NVS anchors as 4 plain floats).

This guard scans the shipped firmware sources (firmware/lib/*.h and
firmware/greenhouse/*.yaml) and fails if any crop name appears in NON-COMMENT
code or config. It codifies the AC5 boundary the same way
scripts/check_migration_rollback_safety.py codifies the migration rule.
Comments may reference crops (design rationale); executable code, config keys,
and string literals may not — that is what would mean crop identity on-chip.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Concrete crop names whose presence in firmware code/config would mean crop
# identity is baked into the controller. The generic word "crop" is intentionally
# excluded — it is allowed in identifiers like crop_band_anchors references.
CROP_TERMS = [
    "orchid",
    "vanda",
    "phalaenopsis",
    "cattleya",
    "dendrobium",
    "paphiopedilum",
    "cannabis",
    "lettuce",
    "strawberry",
    "pepper",
    "jalapeno",
    "tomato",
    "basil",
    "lime",
    "citrus",
    "seedling",
]
CROP_RE = re.compile(r"(?i)\b(" + "|".join(CROP_TERMS) + r")\b")

# Shipped firmware only. firmware/test/* is the off-device test harness (its
# comments legitimately name crops) and is not flashed to the ESP32.
FIRMWARE_SOURCES = sorted(
    [*(REPO / "firmware" / "lib").glob("*.h"), *(REPO / "firmware" / "greenhouse").glob("*.yaml")]
)


def _strip_c_comments(text: str) -> str:
    """Blank out // and /* */ comments, preserving newlines (so line numbers stay
    aligned) and keeping string literals (a crop name in a string literal is still
    crop identity and must be caught)."""
    out: list[str] = []
    i, n = 0, len(text)
    in_block = in_line = False
    in_str: str | None = None
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_block:
            out.append("\n" if c == "\n" else " ")
            if c == "*" and nxt == "/":
                out.append(" ")
                in_block = False
                i += 2
                continue
            i += 1
            continue
        if in_line:
            if c == "\n":
                in_line = False
                out.append("\n")
            i += 1
            continue
        if in_str:
            out.append(c)
            if c == "\\" and nxt:
                out.append(nxt)
                i += 2
                continue
            if c == in_str:
                in_str = None
            i += 1
            continue
        if c == "/" and nxt == "*":
            in_block = True
            i += 2
            continue
        if c == "/" and nxt == "/":
            in_line = True
            i += 2
            continue
        if c in ('"', "'"):
            in_str = c
        out.append(c)
        i += 1
    return "".join(out)


def _strip_yaml_comments(text: str) -> str:
    """Drop comments from ESPHome YAML, which embeds C++ lambda blocks: a YAML '#'
    comment (line-start or whitespace-preceded) AND a C++ '//' line comment inside
    a lambda. Strings are preserved; over-cutting an unquoted URL '//' is harmless
    because the scan only looks for crop tokens (never present after a URL's //)."""
    lines = []
    for line in text.split("\n"):
        in_str: str | None = None
        cut = None
        for j, ch in enumerate(line):
            if in_str:
                if ch == in_str:
                    in_str = None
            elif ch in ('"', "'"):
                in_str = ch
            elif ch == "#" and (j == 0 or line[j - 1] in " \t"):
                cut = j
                break
            elif ch == "/" and j + 1 < len(line) and line[j + 1] == "/":
                cut = j  # C++ line comment inside an embedded lambda
                break
        lines.append(line if cut is None else line[:cut])
    return "\n".join(lines)


def _crop_hits(path: Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    stripped = _strip_c_comments(text) if path.suffix == ".h" else _strip_yaml_comments(text)
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(stripped.split("\n"), start=1):
        for m in CROP_RE.finditer(line):
            hits.append((lineno, m.group(0)))
    return hits


def test_firmware_sources_exist():
    # Fail loudly if the scan target moved — a silent empty scan would be a false pass.
    assert FIRMWARE_SOURCES, "no firmware/lib/*.h or firmware/greenhouse/*.yaml found to scan"
    names = {p.name for p in FIRMWARE_SOURCES}
    assert "greenhouse_logic.h" in names
    assert "greenhouse_solar.h" in names


def test_shipped_firmware_has_no_crop_identity():
    offenders: list[str] = []
    for path in FIRMWARE_SOURCES:
        for lineno, term in _crop_hits(path):
            offenders.append(f"{path.relative_to(REPO)}:{lineno}: crop term '{term}' in non-comment firmware")
    assert not offenders, (
        "Crop identity leaked into the shipped firmware (AC5 violation). Crop "
        "strategy must live above firmware as numeric band anchors:\n  " + "\n  ".join(offenders)
    )


def test_comment_stripper_still_detects_a_planted_crop_token():
    # Self-test: the stripper must NOT blank out crop names in real code/strings,
    # only in comments — otherwise the guard above could pass vacuously.
    sample_h = 'int x = 1; // orchid in a comment is fine\nconst char* c = "vanda";\n'
    stripped = _strip_c_comments(sample_h)
    assert "orchid" not in stripped, "comment crop term should be stripped"
    assert "vanda" in stripped, "string-literal crop term must survive (it is crop identity)"
    sample_yaml = "key: value  # orchid comment\nname: vanda\n"
    stripped_y = _strip_yaml_comments(sample_yaml)
    assert "orchid" not in stripped_y
    assert "vanda" in stripped_y
