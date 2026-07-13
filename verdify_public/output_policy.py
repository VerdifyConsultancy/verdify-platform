"""Publication policy shared by the public API and generated Lab content."""

from __future__ import annotations

import argparse
import base64
import binascii
import html
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, unquote_plus

# These catalog rows remain available to greenhouse control but must not be
# identified in unauthenticated API responses, public pages, search indexes,
# metadata, structured data, or downloadable public datasets.
PUBLIC_CROP_EXCLUDE_SLUGS = frozenset({"cannabis"})
PUBLIC_CROP_REDACTION = "non-public crop"
PUBLIC_CROP_SQL_NAME_PATTERN = (
    r"(^|[^A-Za-z0-9])("
    + "|".join(re.escape(value) for value in sorted(PUBLIC_CROP_EXCLUDE_SLUGS))
    + r")([^A-Za-z0-9]|$)"
)
PUBLIC_DECODE_WINDOW_CHARS = 64 * 1024
PUBLIC_DECODE_OVERLAP_CHARS = 1024
PUBLIC_DECODE_MAX_ROUNDS = 6
PUBLIC_DECODE_MAX_VARIANTS_PER_WINDOW = 24
PUBLIC_DECODE_MAX_BASE64_TOKENS = 64
PUBLIC_DECODE_MAX_BASE64_TOKEN_CHARS = 512
PUBLIC_DECODE_MAX_BASE64_RESULT_CHARS = 2048
PUBLIC_DECODE_MAX_INPUT_CHARS = 4 * 1024 * 1024
JSON_UNICODE_ESCAPE_RE = re.compile(r"\\+u([0-9a-f]{4})", flags=re.IGNORECASE)
URL_ESCAPE_RE = re.compile(r"%[0-9a-f]{2}", flags=re.IGNORECASE)
BASE64_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_+/=-])([A-Za-z0-9_+/-]{8,}={0,2})(?![A-Za-z0-9_+/=-])")
PADDED_BASE64_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_+/=-])[A-Za-z0-9_+/-]{6,}={1,2}(?![A-Za-z0-9_+/=-])")
OVERSIZED_BASE64_TOKEN_RE = re.compile(
    rf"(?<![A-Za-z0-9_+/=-])[A-Za-z0-9_+/-]{{{PUBLIC_DECODE_MAX_BASE64_TOKEN_CHARS + 1},}}"
)
UNPADDED_BASE64_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_+/=-])"
    r"(?=[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_+/=-]))"
    r"(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*[0-9_-])[A-Za-z0-9_-]+"
)
DATA_IMAGE_BASE64_RE = re.compile(
    r"(data:image/(?:png|jpe?g);base64,)[A-Za-z0-9+/]+={0,2}",
    flags=re.IGNORECASE,
)
PUBLIC_CROP_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    + "|".join(re.escape(identifier) for identifier in sorted(PUBLIC_CROP_EXCLUDE_SLUGS, key=len, reverse=True))
    + r")(?![A-Za-z0-9])",
    flags=re.IGNORECASE,
)
PUBLIC_UNPADDED_BASE64_REFERENCES = tuple(
    base64.urlsafe_b64encode(identifier.encode()).decode().rstrip("=")
    for identifier in sorted(PUBLIC_CROP_EXCLUDE_SLUGS)
)
SQL_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class PublicDecodeResult:
    """Bounded textual representations plus whether any decoder bound was hit."""

    variants: tuple[str, ...]
    limit_hit: bool = False


def _sql_alias(value: str) -> str:
    if not SQL_ALIAS_RE.fullmatch(value):
        raise ValueError("invalid SQL alias")
    return value


def public_crop_zone_joins(
    crop_alias: str = "c",
    position_alias: str = "p",
    shelf_alias: str = "sh",
    legacy_zone_alias: str = "legacy_zone",
) -> str:
    """Shared canonical → linked-position → legacy zone resolution joins."""
    crop = _sql_alias(crop_alias)
    position = _sql_alias(position_alias)
    shelf = _sql_alias(shelf_alias)
    legacy = _sql_alias(legacy_zone_alias)
    return (
        f"LEFT JOIN positions {position} ON {position}.id = {crop}.position_id "
        f"AND {position}.greenhouse_id = {crop}.greenhouse_id "
        f"LEFT JOIN shelves {shelf} ON {shelf}.id = {position}.shelf_id "
        f"LEFT JOIN zones {legacy} ON {crop}.zone_id IS NULL "
        f"AND {shelf}.zone_id IS NULL "
        f"AND {legacy}.greenhouse_id = {crop}.greenhouse_id "
        f"AND lower(btrim({legacy}.slug)) = lower(btrim({crop}.zone))"
    )


def public_crop_zone_identity_sql(
    crop_alias: str = "c",
    shelf_alias: str = "sh",
    legacy_zone_alias: str = "legacy_zone",
) -> str:
    """Return the single fail-closed zone identity precedence expression."""
    crop = _sql_alias(crop_alias)
    shelf = _sql_alias(shelf_alias)
    legacy = _sql_alias(legacy_zone_alias)
    return f"COALESCE({crop}.zone_id, {shelf}.zone_id, {legacy}.id)"


def public_crop_sql_predicate(
    slug_expression: str,
    name_expression: str,
    slug_parameter: int,
    name_parameter: int,
) -> str:
    """Shared SQL half of the fail-closed protected-record policy."""
    return (
        f"{slug_expression} IS NOT NULL "
        f"AND btrim({slug_expression}) <> '' "
        f"AND lower(btrim({slug_expression})) <> ALL(${slug_parameter}::text[]) "
        f"AND {name_expression} IS NOT NULL "
        f"AND btrim({name_expression}) <> '' "
        f"AND NOT ({name_expression} ~* ${name_parameter})"
    )


def public_crop_zone_predicate(
    zone_expression: str,
    slug_expression: str,
    name_expression: str,
    slug_parameter: int,
    name_parameter: int,
    *,
    crop_alias: str = "c",
) -> str:
    """Single shared zone-membership plus protected-record predicate."""
    return (
        f"{public_crop_zone_identity_sql(crop_alias)} = {zone_expression} "
        f"AND {public_crop_sql_predicate(slug_expression, name_expression, slug_parameter, name_parameter)}"
    )


def _normalized_identifier(identifier: object) -> str:
    return str(identifier or "").strip().casefold()


def _text_windows(text: str) -> Iterator[str]:
    if len(text) <= PUBLIC_DECODE_WINDOW_CHARS:
        yield text
        return
    step = PUBLIC_DECODE_WINDOW_CHARS - PUBLIC_DECODE_OVERLAP_CHARS
    for start in range(0, len(text), step):
        yield text[start : start + PUBLIC_DECODE_WINDOW_CHARS]


def _decode_base64_token(token: str) -> tuple[str | None, bool]:
    if len(token) > PUBLIC_DECODE_MAX_BASE64_TOKEN_CHARS:
        return None, True
    padded = token + "=" * (-len(token) % 4)
    for altchars in (None, b"-_"):
        try:
            decoded = base64.b64decode(padded, altchars=altchars, validate=True)
            text = decoded.decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
        if len(text) > PUBLIC_DECODE_MAX_BASE64_RESULT_CHARS:
            return None, True
        if not text:
            continue
        if any((ord(char) < 32 and char not in "\t\r\n") or ord(char) > 126 for char in text):
            continue
        return text, False
    return None, False


def _decode_base64_tokens(text: str) -> tuple[str, bool]:
    decoded_count = 0
    limit_hit = False

    def replace(match: re.Match[str]) -> str:
        nonlocal decoded_count, limit_hit
        # Data-URI payloads are typed binary representations. The artifact
        # scanner handles them as images; the shared prose decoder must not
        # reinterpret compressed pixels as text.
        if text[max(0, match.start() - 8) : match.start()].casefold() == ";base64,":
            return match.group(0)
        if decoded_count >= PUBLIC_DECODE_MAX_BASE64_TOKENS:
            limit_hit = True
            return match.group(0)
        decoded, token_limit_hit = _decode_base64_token(match.group(1))
        limit_hit = limit_hit or token_limit_hit
        if decoded is None:
            return match.group(0)
        decoded_count += 1
        return decoded

    return BASE64_TOKEN_RE.sub(replace, text), limit_hit


def _transforms(value: str) -> tuple[list[str], bool]:
    transforms: list[str] = []
    limit_hit = False
    if "\\" in value and JSON_UNICODE_ESCAPE_RE.search(value):
        transforms.append(JSON_UNICODE_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 16)), value))
    if "&" in value and ";" in value:
        transforms.append(html.unescape(value))
    if URL_ESCAPE_RE.search(value):
        transforms.extend((unquote(value), unquote_plus(value)))
    if _has_base64_decode_candidate(value):
        decoded, base64_limit_hit = _decode_base64_tokens(value)
        transforms.append(decoded)
        limit_hit = limit_hit or base64_limit_hit
    return [decoded for decoded in transforms if decoded != value], limit_hit


def _has_base64_decode_candidate(value: str) -> bool:
    if any(encoded in value for encoded in PUBLIC_UNPADDED_BASE64_REFERENCES):
        return True
    if "=" not in value and value.islower():
        return False
    if len(value) > PUBLIC_DECODE_MAX_BASE64_TOKEN_CHARS and OVERSIZED_BASE64_TOKEN_RE.search(value):
        return True
    return bool(PADDED_BASE64_TOKEN_RE.search(value) or UNPADDED_BASE64_TOKEN_RE.search(value))


def public_text_requires_decoding(value: str) -> bool:
    return bool(
        (("\\u" in value or "\\U" in value) and JSON_UNICODE_ESCAPE_RE.search(value))
        or "&#" in value
        or ("%" in value and URL_ESCAPE_RE.search(value))
        or _has_base64_decode_candidate(value)
    )


def decode_public_text(value: object) -> PublicDecodeResult:
    """Return bounded decoding variants and explicit fail-closed limit state."""
    text = str(value or "")
    text = DATA_IMAGE_BASE64_RE.sub(r"\1<binary-image>", text)
    limit_hit = len(text) > PUBLIC_DECODE_MAX_INPUT_CHARS
    if limit_hit:
        text = text[:PUBLIC_DECODE_MAX_INPUT_CHARS]
    variants: list[str] = []
    for window in _text_windows(text):
        queue: list[tuple[str, int]] = [(window, 0)]
        seen: set[str] = set()
        while queue:
            current, depth = queue.pop(0)
            if current in seen:
                continue
            if len(seen) >= PUBLIC_DECODE_MAX_VARIANTS_PER_WINDOW:
                limit_hit = True
                break
            seen.add(current)
            variants.append(current)
            decoded_values, transform_limit_hit = _transforms(current)
            limit_hit = limit_hit or transform_limit_hit
            if depth >= PUBLIC_DECODE_MAX_ROUNDS:
                if any(decoded not in seen for decoded in decoded_values):
                    limit_hit = True
                continue
            for decoded in decoded_values:
                if decoded not in seen:
                    queue.append((decoded, depth + 1))
            if len(queue) + len(seen) > PUBLIC_DECODE_MAX_VARIANTS_PER_WINDOW:
                limit_hit = True
                queue = queue[: max(0, PUBLIC_DECODE_MAX_VARIANTS_PER_WINDOW - len(seen))]
    return PublicDecodeResult(tuple(variants), limit_hit)


def iter_decoded_public_text_variants(value: object) -> Iterator[str]:
    """Yield bounded decoding variants used by API redaction and artifact scans."""
    yield from decode_public_text(value).variants


def is_public_crop(identifier: object) -> bool:
    """Return whether a non-empty canonical catalog identifier is publishable."""
    normalized = _normalized_identifier(identifier)
    return bool(normalized) and not contains_non_public_crop_reference(identifier)


def contains_non_public_crop_reference(value: object) -> bool:
    """Return whether prose or a path identifies an excluded crop."""
    decoded = decode_public_text(value)
    return decoded.limit_hit or any(PUBLIC_CROP_REFERENCE_RE.search(variant) for variant in decoded.variants)


def is_public_crop_record(
    catalog_slug: object,
    display_name: object = None,
    *,
    occupied: bool = True,
) -> bool:
    """Classify a crop-bearing row, permitting truly empty position rows only."""
    if not occupied:
        return True
    if not is_public_crop(catalog_slug):
        return False
    normalized_name = _normalized_identifier(display_name)
    return bool(normalized_name) and not contains_non_public_crop_reference(display_name)


def redact_non_public_crop_references(value: object) -> str:
    """Remove excluded crop identifiers from otherwise-public prose."""
    text = str(value or "")
    original = text
    text = PUBLIC_CROP_REFERENCE_RE.sub(PUBLIC_CROP_REDACTION, text)
    decoded = decode_public_text(text)
    if decoded.limit_hit or any(PUBLIC_CROP_REFERENCE_RE.search(variant) for variant in decoded.variants):
        return PUBLIC_CROP_REDACTION
    if text != original:
        return text
    return text


def redact_public_data(value: object) -> object:
    """Recursively redact strings while preserving public response value types."""
    if isinstance(value, str):
        return redact_non_public_crop_references(value)
    if isinstance(value, dict):
        return {
            redact_non_public_crop_references(key) if isinstance(key, str) else key: redact_public_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_public_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_public_data(item) for item in value)
    return value


def redact_file(path: Path) -> bool:
    """Redact a UTF-8 text file in place; return whether it changed."""
    original = path.read_text(encoding="utf-8")
    redacted = redact_non_public_crop_references(original)
    if redacted == original:
        return False
    path.write_text(redacted, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Verdify's public prose redaction policy to text files.")
    parser.add_argument("--in-place", action="store_true", help="Rewrite each file in place.")
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    if not args.in_place:
        parser.error("--in-place is required")
    for path in args.files:
        redact_file(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
