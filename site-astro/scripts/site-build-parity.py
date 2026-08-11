#!/usr/bin/env python3
"""Build and compare framework-neutral static-site parity manifests.

The Lab migration needs to prove that a candidate build preserves the public
contract of the current build without coupling that proof to Quartz or Astro.
This tool inventories rendered HTML and referenced assets only. It is
deterministic and offline; PNG decoding is stdlib-native and other image
formats use Pillow when it is already available, otherwise failing closed.

Examples::

    python scripts/site-build-parity.py manifest public-quartz --snapshot-root .snapshot -o quartz.json
    python scripts/site-build-parity.py manifest dist-astro --snapshot-root .snapshot -o astro.json
    python scripts/site-build-parity.py compare quartz.json astro.json --snapshot-root .snapshot -o parity.json
    python scripts/site-build-parity.py compare quartz.json astro.json --snapshot-root .snapshot --allow-provisional -o parity.json

``manifest`` preflights the complete tree without following links or reading
file content, with closed limits for total/per-directory entries, directory
count/depth, path bytes, asset count, and aggregate bytes. It then exits
non-zero for unsafe entries, missing referenced assets, invalid feeds/indexes,
or other integrity findings while still writing a safe diagnostic manifest. It
inventories semantic/data/media files plus every
referenced stylesheet, script, module, and WebAssembly dependency. Unreferenced
CSS/JS/WASM deliberately remains a mandatory build/browser-gate responsibility.
Local HLS playlists are parsed without network access into a closed dependency
graph; traversal, non-release URLs, credential queries, and missing variants,
segments, maps, subtitles, or keys are blocking integrity failures.

``compare`` treats the first manifest as the required baseline: missing routes,
aliases, content, feed/sitemap entries, Grafana source roles, runtime bytes, or
other semantic values fail; additions are reported separately and do not fail
parity. RSS, Atom, and sitemap entries are parsed and canonicalized, and sitemap
URLs must equal the canonical indexable route set (aliases and noindex pages are
excluded). Every feed URL must also name a same-origin canonical indexable
route, although a feed may intentionally cover only a subset of those routes.
Grafana evidence keeps ``src``, lazy ``data-src``, ``data-live-src``,
``data-iframe-src``, and ``data-image-src`` as distinct source roles and rejects
conflicting live identities, panels, variables, or normalized time ranges. Its
fallback must be a release-local, regular, bounded, structurally decoded image;
network, opaque, traversal, symlink, extension/MIME, and signature substitutes
fail closed.
Alias evidence includes the exact refresh target (including query/fragment),
canonical, robots policy, and source. Robots groups/directives/sitemaps, typed
runtime preloads, effective form ownership/submission behavior, distinct media
source roles, and parsed search-index metadata are structural evidence.

Text comparison is deliberately order-sensitive: all baseline semantic tokens
must remain in order, while candidate tokens may be inserted anywhere. This is
a structural gate. External-CSS visibility, browser interaction, responsive
Grafana fallback behavior, and visual presentation require separate browser and
visual gates. ``compare --exceptions FILE`` accepts only v2 exceptions bound to
the canonical baseline-manifest digest and the full current failure digest,
with an allowed category, concrete owner, activation issue and explicit activation
attestation. Tree/integrity/duplicate failures are never waivable.

Every manifest and comparison must re-read the same bounded snapshot root. The
manifest records the exact content-manifest and attestation byte digests; a
mismatch is an unwaivable failure. The currently supported attestation contract
is explicitly ``provisional-only`` and never activation-eligible. A future
active immutable filesystem/object-store attestation requires its own trusted
resolver before this tool may accept it. Accordingly, a structurally compatible
comparison of the current snapshot exits 3 instead of claiming activation.
``--allow-provisional`` changes that diagnostic exit to 0 but leaves the report
explicitly non-activation-eligible. Structural mismatch exits 1; malformed input
or operational failure exits 2.
"""

from __future__ import annotations

import argparse
import binascii
import fcntl
import hashlib
import html
import io
import json
import os
import posixpath
import re
import stat
import struct
import sys
import unicodedata
import warnings
import xml.etree.ElementTree as ET
import zlib
from collections import Counter, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

CONTRACT = "verdify.static-site-parity"
SCHEMA_VERSION = 2
EXCEPTIONS_CONTRACT = "verdify.static-site-parity-exceptions"
EXCEPTIONS_SCHEMA_VERSION = 2
DEFAULT_ORIGIN = "https://lab.verdify.ai"
SNAPSHOT_ATTESTATION_CONTRACT = "verdify.lab-stage-sanitized-snapshot"
SNAPSHOT_ATTESTATION_SCHEMA_VERSION = 1
SNAPSHOT_ATTESTATION_MAX_BYTES = 64 * 1024
SNAPSHOT_MANIFEST_MAX_BYTES = 8 * 1024 * 1024
SNAPSHOT_MANIFEST_MAX_FILES = 10_000
SEMANTIC_ASSET_KINDS = frozenset({"download", "media", "metadata", "public"})
REQUIRED_RUNTIME_ASSET_KINDS = frozenset(
    {
        "module",
        "modulepreload",
        "preload-fetch",
        "preload-font",
        "preload-image",
        "preload-script",
        "preload-style",
        "preload-wasm",
        "script",
        "search-index",
        "search-runtime",
        "stylesheet",
        "wasm",
    }
)
HLS_ASSET_KINDS = frozenset(
    {
        "hls-key",
        "hls-map",
        "hls-media",
        "hls-playlist",
        "hls-rendition",
        "hls-segment",
        "hls-subtitle",
        "hls-subtitle-playlist",
        "hls-variant",
    }
)
EXCEPTION_CATEGORIES = frozenset({"active-content-improvement", "privacy", "security", "sentinel", "seo"})
GRAFANA_LIVE_SOURCE_ATTRIBUTES = ("data-live-src", "data-iframe-src")
GRAFANA_FALLBACK_SOURCE_ATTRIBUTES = ("data-image-src", "data-src")
GRAFANA_SOURCE_ATTRIBUTES = (*GRAFANA_LIVE_SOURCE_ATTRIBUTES, *GRAFANA_FALLBACK_SOURCE_ATTRIBUTES, "src")
NATIVE_VISUAL_STATES = frozenset(
    {
        "closed-details",
        "closed-dialog",
        "closed-popover",
        "hidden-attribute",
        "inline-content-visibility-hidden",
        "inline-display-none",
        "inline-visibility-hidden",
        "input-type-hidden",
    }
)
REVEALABLE_VISUAL_STATES = frozenset({"closed-details", "closed-dialog", "closed-popover"})
NATIVE_ACCESSIBILITY_STATES = frozenset({"aria-hidden"})
NATIVE_INTERACTIVITY_STATES = frozenset({"disabled-control", "disabled-fieldset", "inert"})
FIELDSET_DISABLED_CONTROL_TAGS = frozenset(
    {"button", "fieldset", "form", "input", "optgroup", "option", "select", "textarea"}
)
PAGE_LOCATIONS = frozenset({"body", "content", "footer", "head", "header", "nav"})
ASSET_REFERENCE_KINDS = frozenset(
    {
        "download",
        *HLS_ASSET_KINDS,
        "html",
        "link",
        "media",
        "metadata",
        "module",
        "modulepreload",
        "preload-fetch",
        "preload-font",
        "preload-image",
        "preload-script",
        "preload-style",
        "preload-wasm",
        "public",
        "script",
        "search-index",
        "search-runtime",
        "stylesheet",
        "wasm",
    }
)
PRELOAD_AS_KINDS = {
    "fetch": "preload-fetch",
    "font": "preload-font",
    "image": "preload-image",
    "script": "preload-script",
    "style": "preload-style",
    "wasm": "preload-wasm",
}
RUNTIME_ATTRIBUTE_NAMES = (
    "crossorigin",
    "fetchpriority",
    "imagesizes",
    "imagesrcset",
    "integrity",
    "media",
    "referrerpolicy",
    "type",
)
ROBOT_META_NAMES = frozenset(
    {
        "bingbot",
        "googlebot",
        "googlebot-image",
        "googlebot-news",
        "googlebot-video",
        "slurp",
        "yandex",
    }
)
IMAGE_MIME_BY_SUFFIX = {
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
FORM_METHODS = frozenset({"dialog", "get", "post"})
FORM_ENCTYPES = frozenset({"application/x-www-form-urlencoded", "multipart/form-data", "text/plain"})
BUTTON_TYPES = frozenset({"button", "reset", "submit"})
INPUT_TYPES = frozenset(
    {
        "button",
        "checkbox",
        "color",
        "date",
        "datetime-local",
        "email",
        "file",
        "hidden",
        "image",
        "month",
        "number",
        "password",
        "radio",
        "range",
        "reset",
        "search",
        "submit",
        "tel",
        "text",
        "time",
        "url",
        "week",
    }
)
DEFAULT_LIMITS = {
    "asset_count": 500_000,
    "asset_bytes": 512 * 1024 * 1024,
    "directory_depth": 256,
    "directory_entries": 100_000,
    "hls_bytes": 8 * 1024 * 1024,
    "hls_dependencies": 100_000,
    "hls_depth": 64,
    "hls_playlists": 10_000,
    "html_bytes": 32 * 1024 * 1024,
    "html_depth": 256,
    "html_elements": 1_000_000,
    "html_files": 100_000,
    "image_bytes": 64 * 1024 * 1024,
    "image_pixels": 100_000_000,
    "image_decoded_bytes": 128 * 1024 * 1024,
    "image_frames": 1000,
    "json_bytes": 64 * 1024 * 1024,
    "json_depth": 128,
    "json_nodes": 1_000_000,
    "manifest_bytes": 128 * 1024 * 1024,
    "manifest_depth": 128,
    "manifest_nodes": 2_000_000,
    "robots_bytes": 1024 * 1024,
    "path_bytes": 4096,
    "tree_bytes": 4 * 1024 * 1024 * 1024,
    "tree_directories": 100_000,
    "tree_entries": 500_000,
    "xml_bytes": 32 * 1024 * 1024,
    "xml_depth": 128,
    "xml_entries": 100_000,
}
# Reading a serialized parity manifest is a separate trust boundary from the
# source/parser limits recorded inside that manifest.  The frozen 321-page
# Astro stage candidate contains 2,320,156 JSON nodes, so the former 2,000,000
# reader ceiling rejected the comparator's own valid output.  Three million
# leaves 29% bounded headroom for that immutable corpus while the independent
# 128 MiB byte and depth-128 limits remain in force.
MANIFEST_INPUT_MAX_NODES = 3_000_000
HLS_PLAYLIST_SUFFIXES = frozenset({".m3u", ".m3u8"})
HLS_MEDIA_SUFFIXES = frozenset({".aac", ".key", ".m4s", ".mp3", ".mp4", ".srt", ".ts", ".vtt"})
HLS_DEPENDENCY_ROLES = frozenset({"key", "map", "rendition", "segment", "subtitle", "subtitle-playlist", "variant"})
HLS_ATTRIBUTE_LIST_TAGS = frozenset(
    {
        "#EXT-X-CONTENT-STEERING",
        "#EXT-X-DATERANGE",
        "#EXT-X-DEFINE",
        "#EXT-X-I-FRAME-STREAM-INF",
        "#EXT-X-KEY",
        "#EXT-X-MAP",
        "#EXT-X-MEDIA",
        "#EXT-X-PART",
        "#EXT-X-PART-INF",
        "#EXT-X-PRELOAD-HINT",
        "#EXT-X-RENDITION-REPORT",
        "#EXT-X-SERVER-CONTROL",
        "#EXT-X-SESSION-DATA",
        "#EXT-X-SESSION-KEY",
        "#EXT-X-START",
    }
)
HLS_TARGET_SUFFIXES = {
    "key": frozenset({".key"}),
    "map": frozenset({".m4s", ".mp4"}),
    "rendition": HLS_PLAYLIST_SUFFIXES,
    "segment": frozenset({".aac", ".m4s", ".mp3", ".mp4", ".ts"}),
    "subtitle": frozenset({".srt", ".vtt"}),
    "subtitle-playlist": HLS_PLAYLIST_SUFFIXES,
    "variant": HLS_PLAYLIST_SUFFIXES,
}
HLS_MIME_BY_SUFFIX = {
    ".aac": "audio/aac",
    ".key": "application/octet-stream",
    ".m4s": "video/mp4",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".srt": "application/x-subrip",
    ".ts": "video/mp2t",
    ".vtt": "text/vtt",
}
HLS_ROLE_MIME = {
    ("key", ".key"): "application/octet-stream",
    ("map", ".m4s"): "video/mp4",
    ("map", ".mp4"): "video/mp4",
    ("segment", ".aac"): "audio/aac",
    ("segment", ".m4s"): "video/mp4",
    ("segment", ".mp3"): "audio/mpeg",
    ("segment", ".mp4"): "video/mp4",
    ("segment", ".ts"): "video/mp2t",
    ("subtitle", ".srt"): "application/x-subrip",
    ("subtitle", ".vtt"): "text/vtt",
}
HLS_POLICY = {
    "syntax_profile": "RFC 8216 floor plus draft-pantos-hls-rfc8216bis-22 supported-tag subset",
    "unsupported_key_methods": ["AES-256-GCM", "SAMPLE-AES-CTR"],
    "daterange_client_attributes": (
        "X-* quoted-string, hexadecimal-sequence, or decimal-floating-point; URI-looking values forbidden"
    ),
    "daterange_evidence": "ordered name/form/value; credential-bearing values are retained by SHA-256 digest",
    "media_instream_id": "bis-22 quoted ASCII identifier; CLOSED-CAPTIONS retains the exact CC/SERVICE enum",
    "network_fetch": False,
    "release_root_contained": True,
    "allowed_references": "root-relative-or-playlist-relative-local",
    "forbidden_references": [
        "filesystem/backslash traversal",
        "network/internal/external origins",
        "userinfo credentials",
        "query credentials or signatures",
    ],
}
DOWNLOAD_SUFFIXES = frozenset(
    {
        ".7z",
        ".csv",
        ".doc",
        ".docx",
        ".geojson",
        ".gz",
        ".ics",
        ".json",
        ".ods",
        ".pdf",
        ".tar",
        ".tsv",
        ".txt",
        ".xls",
        ".xlsx",
        ".xml",
        ".yaml",
        ".yml",
        ".zip",
    }
)
PUBLIC_SEMANTIC_SUFFIXES = frozenset(
    {
        *DOWNLOAD_SUFFIXES,
        ".avif",
        ".bmp",
        ".flac",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".m4a",
        ".m4v",
        ".mov",
        ".mp3",
        ".mp4",
        ".oga",
        ".ogg",
        ".ogv",
        ".otf",
        ".parquet",
        ".png",
        ".svg",
        ".ttf",
        ".wav",
        ".webm",
        ".webp",
        ".woff",
        ".woff2",
    }
)
SUPPRESSED_CONTENT_TAGS = frozenset({"footer", "header", "nav", "noscript", "script", "style", "svg", "template"})
TEXT_BOUNDARY_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "caption",
        "dd",
        "details",
        "dialog",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "tbody",
        "td",
        "tfoot",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
FEATURE_NAMES = ("breadcrumbs", "dark", "downloads", "katex", "reader", "robots", "rss", "search", "sitemap")
TOKEN_RE = re.compile(r"[\w]+(?:[./:%+\-][\w]+)*|[^\w\s]", re.UNICODE)
META_REFRESH_RE = re.compile(
    r"^\s*(?P<delay>\d+(?:\.\d+)?)\s*;\s*url\s*=\s*"
    r"(?:\"(?P<double>[^\"]*)\"|'(?P<single>[^']*)'|(?P<bare>.+?))\s*$",
    re.IGNORECASE,
)
GRAFANA_PATH_RE = re.compile(r"^/(?:render/)?(?:d-solo|d)/([^/]+)(?:/|$)", re.IGNORECASE)
GRAFANA_ALLOWED_ORIGIN = "https://graphs.verdify.ai"
GRAFANA_MAX_QUERY_PAIRS = 64
GRAFANA_MAX_QUERY_KEY_BYTES = 128
GRAFANA_MAX_QUERY_VALUE_BYTES = 4096
GRAFANA_MAX_QUERY_BYTES = GRAFANA_MAX_QUERY_PAIRS * (GRAFANA_MAX_QUERY_KEY_BYTES + GRAFANA_MAX_QUERY_VALUE_BYTES + 2)
GRAFANA_MAX_PATH_BYTES = 4096
GRAFANA_MAX_UID_BYTES = 128
WILDCARD_RE = re.compile(r"[*?\[\]]")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ACTIVATION_ISSUE_RE = re.compile(
    r"^(?:#[1-9][0-9]*|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*|"
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[1-9][0-9]*)$"
)
VERIFICATION_SCOPE = {
    "kind": "structural-static-build",
    "snapshot_boundary": {
        "kind": "cooperative-root-directory-flock",
        "acquisition": "nonblocking-exclusive-before-preflight-held-through-final-evidence",
        "producer_requirement": "every compliant producer or mutator must hold the same root-directory flock",
        "local_evidence_status": "provisional-only",
        "activation_eligible": False,
        "mandatory_activation_boundary": "active immutable filesystem/object-store snapshot attestation",
        "outside_guarantee": "uncooperative hostile same-UID mutation remains outside userspace guarantees",
    },
    "text_policy": (
        "Baseline semantic tokens must remain in their original order. Candidate tokens may be inserted anywhere; "
        "removal or reordering of baseline tokens fails."
    ),
    "proves": [
        "rendered static structure and metadata",
        "required local asset presence and byte identity",
        "declared interactive controls plus required search/feed/index artifacts",
        "native visual visibility separately from inherited inert interactivity",
        "same-origin canonical indexable URLs in RSS, Atom, and sitemap documents",
        "Grafana live roles plus decoded release-local fallback images including data-src",
        "closed local HLS dependency graphs with exact URL roles and bytes",
        "exact alias refresh/canonical/robots semantics",
        "parsed Pagefind metadata and robots groups/directives/sitemaps",
        "typed preload and modulepreload references plus target bytes",
    ],
    "requires_separate_browser_visual_gates": [
        "visibility or behavior changed by external CSS",
        "interactive search, dark-mode, and reader-mode behavior",
        "Grafana live loading, responsive fallback selection, and visual rendering",
        "layout, fonts, accessibility interaction, and responsive presentation",
    ],
    "outside_semantic_parity_but_mandatory_build_browser_contract": [
        "unreferenced CSS, JavaScript, module, and WebAssembly files",
        "external stylesheet effects and client-side behavior",
        "media decoding quality after HLS dependency and byte closure passes",
    ],
}


def normalize_text(value: str) -> str:
    """Normalize rendered text without weakening punctuation or numeric values."""
    return " ".join(unicodedata.normalize("NFC", html.unescape(value)).split())


COMPATIBLE_PUNCTUATION = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "…": "...",
        "→": "->",
        "←": "<-",
        "⇒": "=>",
        "⇐": "<=",
    }
)
PLACEHOLDER_METADATA_TEXT = frozenset({"no description provided"})


def compatible_text(value: str) -> str:
    translated = normalize_text(value).translate(COMPATIBLE_PUNCTUATION)
    # Quartz's smartypants transform treats doubled backticks in rendered
    # prose as an opening double quote. Astro preserves the frozen source
    # bytes, so normalize that presentation-only difference without weakening
    # single-backtick/code-token comparisons.
    return re.sub(r"`{2,}", '"', translated)


def meaningful_metadata_text(value: Any) -> str:
    normalized = compatible_text(value) if isinstance(value, str) else ""
    return "" if normalized.casefold() in PLACEHOLDER_METADATA_TEXT else normalized


def _is_not_found_metadata_upgrade(route: str, field: str, baseline: Any, candidate: Any) -> bool:
    """Recognize the bounded SEO correction applied to the generated 404 page."""

    if route != "/404":
        return False
    if field == "canonical":
        return baseline == "/" and candidate == "/404"
    if field == "noindex":
        return baseline is False and candidate is True
    return False


def semantic_tokens(value: str) -> list[str]:
    return TOKEN_RE.findall(compatible_text(value))


def _inline_style_properties(value: str) -> dict[str, str]:
    value = re.sub(r"/\*.*?\*/", "", value, flags=re.DOTALL)
    declarations: list[str] = []
    start = 0
    quote_character = ""
    parentheses = 0
    for index, character in enumerate(value):
        if quote_character:
            if character == quote_character and (index == 0 or value[index - 1] != "\\"):
                quote_character = ""
        elif character in {'"', "'"}:
            quote_character = character
        elif character == "(":
            parentheses += 1
        elif character == ")" and parentheses:
            parentheses -= 1
        elif character == ";" and not parentheses:
            declarations.append(value[start:index])
            start = index + 1
    declarations.append(value[start:])
    properties: dict[str, tuple[bool, str]] = {}
    for declaration in declarations:
        raw_property, separator, raw_value = declaration.partition(":")
        property_name = raw_property.strip().lower()
        if not separator or not re.fullmatch(r"-?[a-z][a-z0-9-]*", property_name):
            continue
        property_value = raw_value.strip().lower()
        important = bool(re.search(r"!\s*important\s*$", property_value))
        property_value = re.sub(r"!\s*important\s*$", "", property_value).strip()
        current = properties.get(property_name)
        if current is None or important or not current[0]:
            properties[property_name] = (important, property_value)
    return {key: item[1] for key, item in properties.items()}


def native_visual_states(tag: str, attrs: dict[str, str]) -> tuple[str, ...]:
    style = _inline_style_properties(attrs.get("style", ""))
    states: list[str] = []
    if "hidden" in attrs:
        states.append("hidden-attribute")
    if style.get("display") == "none":
        states.append("inline-display-none")
    if style.get("visibility") in {"collapse", "hidden"}:
        states.append("inline-visibility-hidden")
    if style.get("content-visibility") == "hidden":
        states.append("inline-content-visibility-hidden")
    if tag == "dialog" and "open" not in attrs:
        states.append("closed-dialog")
    if tag == "details" and "open" not in attrs:
        states.append("closed-details")
    if "popover" in attrs:
        states.append("closed-popover")
    if tag == "input" and attrs.get("type", "").lower() == "hidden":
        states.append("input-type-hidden")
    return tuple(sorted(states))


def native_accessibility_states(attrs: dict[str, str]) -> tuple[str, ...]:
    return ("aria-hidden",) if attrs.get("aria-hidden", "").strip().lower() == "true" else ()


def native_interactivity_states(tag: str, attrs: dict[str, str]) -> tuple[str, ...]:
    states: list[str] = []
    if "inert" in attrs:
        states.append("inert")
    if tag == "fieldset" and "disabled" in attrs:
        states.append("disabled-fieldset")
    elif tag in {"button", "input", "option", "select", "textarea"} and "disabled" in attrs:
        states.append("disabled-control")
    return tuple(states)


def native_visibility_states(tag: str, attrs: dict[str, str]) -> tuple[str, ...]:
    """Compatibility helper returning every native visibility/interaction state."""
    return tuple(sorted({*native_visual_states(tag, attrs), *native_interactivity_states(tag, attrs)}))


def element_is_hidden(attrs: dict[str, str], tag: str = "") -> bool:
    return bool(native_visual_states(tag, attrs))


def _parse_srcset(value: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    cursor = 0
    while cursor < len(value):
        while cursor < len(value) and value[cursor] in " \t\r\n,":
            cursor += 1
        if cursor >= len(value):
            break
        start = cursor
        is_data = value[cursor : cursor + 5].lower() == "data:"
        while cursor < len(value) and not value[cursor].isspace() and (is_data or value[cursor] != ","):
            cursor += 1
        source = value[start:cursor].strip()
        while cursor < len(value) and value[cursor].isspace():
            cursor += 1
        descriptor_start = cursor
        while cursor < len(value) and value[cursor] != ",":
            cursor += 1
        descriptor = " ".join(value[descriptor_start:cursor].split())
        if source:
            candidates.append((source, descriptor))
        if cursor < len(value):
            cursor += 1
    return candidates


def _runtime_reference(
    *,
    attrs: dict[str, str],
    href: str,
    kind: str,
    rel: str,
    route: str,
    origin: str,
    base_url: str | None = None,
) -> dict[str, str]:
    imagesrcset = ""
    if attrs.get("imagesrcset"):
        entries: list[str] = []
        for raw_source, descriptor in _parse_srcset(attrs["imagesrcset"]):
            if raw_source:
                normalized = normalize_reference(raw_source, route=route, origin=origin, base_url=base_url)
                entries.append(f"{normalized} {descriptor}".rstrip())
        imagesrcset = ", ".join(entries)
    nonce_digest = ""
    if "nonce" in attrs:
        nonce_digest = f"sha256:{hashlib.sha256(attrs['nonce'].encode()).hexdigest()}"
    crossorigin = ""
    if "crossorigin" in attrs:
        raw_crossorigin = attrs["crossorigin"].strip().lower()
        crossorigin = "use-credentials" if raw_crossorigin == "use-credentials" else "anonymous"
    return {
        "rel": rel,
        "as": attrs.get("as", "").strip().lower(),
        "href": href,
        "kind": kind,
        "type": attrs.get("type", "").strip().lower(),
        "crossorigin": crossorigin,
        "integrity": " ".join(attrs.get("integrity", "").split()),
        "media": attrs.get("media", "").strip(),
        "referrerpolicy": attrs.get("referrerpolicy", "").strip().lower(),
        "fetchpriority": attrs.get("fetchpriority", "").strip().lower(),
        "imagesrcset": imagesrcset,
        "imagesizes": attrs.get("imagesizes", "").strip(),
        "nonce_digest": nonce_digest,
    }


def normalize_route(path: str) -> str:
    """Return a canonical root-relative route with no non-root trailing slash."""
    path = _normalize_percent_component(path or "/", safe="/!$&'()*+,;=:@-._~")
    path = "/" + path.lstrip("/")
    normalized = posixpath.normpath(path)
    if normalized == ".":
        normalized = "/"
    return normalized if normalized == "/" else normalized.rstrip("/")


def _normalized_netloc(parts) -> str:
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"invalid URL port: {exc}") from exc
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port and not (
        (parts.scheme.lower() == "http" and port == 80) or (parts.scheme.lower() == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    return host


def normalize_origin(origin: str) -> str:
    """Return one exact origin, rejecting URL components that are not origin state."""
    value = origin.strip()
    parts = urlsplit(value)
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("origin must be an absolute http(s) origin without credentials, path, query, or fragment")
    return urlunsplit((parts.scheme.lower(), _normalized_netloc(parts), "", "", ""))


_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
CREDENTIAL_QUERY_RE = re.compile(
    r"(?:^|[-_.])(auth|authorization|credential|key|pass|password|secret|sig|signature|token)(?:$|[-_.])",
    re.IGNORECASE,
)


def _normalize_percent_component(value: str, *, safe: str) -> str:
    """Normalize without decoding reserved octets or changing query ordering."""
    allowed = _UNRESERVED | frozenset(safe)
    rendered: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if (
            character == "%"
            and index + 2 < len(value)
            and re.fullmatch(r"[0-9A-Fa-f]{2}", value[index + 1 : index + 3])
        ):
            byte = int(value[index + 1 : index + 3], 16)
            decoded = chr(byte)
            rendered.append(decoded if decoded in _UNRESERVED else f"%{byte:02X}")
            index += 3
            continue
        if character in allowed:
            rendered.append(character)
        else:
            rendered.extend(f"%{byte:02X}" for byte in character.encode("utf-8"))
        index += 1
    return "".join(rendered)


def _document_base(origin: str, route: str) -> str:
    route = normalize_route(route)
    final_segment = route.rsplit("/", 1)[-1]
    suffix = route if "." in final_segment else ("/" if route == "/" else f"{route}/")
    return f"{origin.rstrip('/')}{suffix}"


class CredentialBearingUrlError(ValueError):
    """Fixed-message URL rejection carrying only a non-reversible digest."""

    def __init__(self, value: str) -> None:
        self.reference_digest = f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"
        super().__init__("credential-bearing URLs are forbidden")


def normalize_non_html_reference(
    value: str,
    *,
    route: str,
    origin: str,
    route_like: bool = False,
) -> str:
    """Normalize a structural URL without ever reflecting protected credentials."""
    inspected = html.unescape(value.strip())
    parts = urlsplit(inspected)
    if (
        parts.username is not None
        or parts.password is not None
        or any(CREDENTIAL_QUERY_RE.search(key) for key, _item in parse_qsl(parts.query, keep_blank_values=True))
    ):
        raise CredentialBearingUrlError(inspected)
    return normalize_reference(inspected, route=route, origin=origin, route_like=route_like)


def normalize_reference(
    value: str,
    *,
    route: str,
    origin: str,
    route_like: bool = False,
    base_url: str | None = None,
) -> str:
    """Resolve and normalize an HTML URL while retaining external origins."""
    value = html.unescape(value.strip())
    if not value:
        return ""
    lowered = value.lower()
    if lowered.startswith(("data:", "javascript:")):
        return value
    if lowered.startswith(("mailto:", "tel:")):
        scheme, rest = value.split(":", 1)
        return f"{scheme.lower()}:{rest}"

    origin = normalize_origin(origin)
    origin_parts = urlsplit(origin)
    absolute = urljoin(base_url or _document_base(origin, route), value)
    parts = urlsplit(absolute)
    if parts.username is not None or parts.password is not None:
        raise CredentialBearingUrlError(value)
    scheme = parts.scheme.lower()
    netloc = _normalized_netloc(parts)
    path = _normalize_percent_component(parts.path or "/", safe="/!$&'()*+,;=:@-._~")
    if route_like:
        path = normalize_route(path)
    query = _normalize_percent_component(parts.query, safe="!$&'()*+,;=:@/?-._~")
    fragment = _normalize_percent_component(parts.fragment, safe="!$&'()*+,;=:@/?-._~")
    if scheme == origin_parts.scheme.lower() and netloc == _normalized_netloc(origin_parts):
        return urlunsplit(("", "", path, query, fragment))
    return urlunsplit((scheme, netloc, path, query, fragment))


def route_from_reference(value: str, *, route: str, origin: str) -> str:
    normalized = normalize_reference(value, route=route, origin=origin, route_like=True)
    parts = urlsplit(normalized)
    if parts.scheme or parts.netloc:
        return normalized
    return normalize_route(parts.path)


def physical_route(relative_html: Path) -> str:
    posix = relative_html.as_posix()
    if posix == "index.html":
        return "/"
    if relative_html.name == "index.html":
        return normalize_route("/" + relative_html.parent.as_posix())
    return normalize_route("/" + posix)


def is_local_reference(value: str) -> bool:
    parts = urlsplit(value)
    return not parts.scheme and not parts.netloc and bool(parts.path) and not value.startswith(("mailto:", "tel:"))


def _effective_control_type(tag: str, raw_type: str) -> str:
    value = raw_type.strip().lower()
    if tag == "button":
        return value if value in BUTTON_TYPES else "submit"
    if tag == "input":
        return value if value in INPUT_TYPES else "text"
    return value


def _effective_form_method(raw_method: str) -> str:
    value = raw_method.strip().lower()
    return value if value in FORM_METHODS else "get"


def _effective_form_enctype(raw_enctype: str) -> str:
    value = raw_enctype.strip().lower()
    return value if value in FORM_ENCTYPES else "application/x-www-form-urlencoded"


def canonical_json(value: Any) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_json(value).encode()).hexdigest()}"


_HLS_POLICY_AUTHORITY_JSON = canonical_json(HLS_POLICY)
_VERIFICATION_SCOPE_AUTHORITY_JSON = canonical_json(VERIFICATION_SCOPE)


def _authority_copy(serialized: str) -> dict[str, Any]:
    """Return a fresh JSON copy from one immutable canonical authority."""

    value = json.loads(serialized)
    if not isinstance(value, dict):  # pragma: no cover - module-owned authorities are objects
        raise RuntimeError("invalid internal policy authority")
    return value


def _matches_canonical_authority(value: Any, serialized: str) -> bool:
    """Compare JSON types and values exactly against a bounded immutable authority."""

    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                return False
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
        elif type(item) not in {bool, float, int, str, type(None)}:
            return False
    try:
        return canonical_json(value) == serialized
    except (OverflowError, RecursionError, TypeError, ValueError):
        return False


def _verification_scope_for(source_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return static policy with status derived from the verified source binding."""

    scope = _authority_copy(_VERIFICATION_SCOPE_AUTHORITY_JSON)
    boundary = scope["snapshot_boundary"]
    boundary["local_evidence_status"] = source_snapshot["evidence_status"]
    boundary["activation_eligible"] = source_snapshot["activation_eligible"]
    return scope


def _json_deep_copy(value: Any) -> Any:
    """Break all JSON-container aliases at a public result boundary."""

    return json.loads(canonical_json(value))


def _resolved_limits(overrides: dict[str, int] | None) -> dict[str, int]:
    limits = dict(DEFAULT_LIMITS)
    if overrides is None:
        return limits
    if not isinstance(overrides, dict):
        raise ValueError("limits must be a mapping of known limit names to positive integers")
    unknown = set(overrides) - set(DEFAULT_LIMITS)
    if unknown:
        raise ValueError(f"unknown limits: {sorted(unknown)}")
    for name, value in overrides.items():
        if type(value) is not int or value < 1:
            raise ValueError(f"limit {name!r} must be a positive integer")
        limits[name] = value
    return limits


def _json_shape(value: Any, *, maximum_depth: int, maximum_nodes: int) -> tuple[int, int]:
    pending = [(value, 1)]
    seen_containers: set[int] = set()
    nodes = 0
    depth_seen = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        depth_seen = max(depth_seen, depth)
        if nodes > maximum_nodes:
            raise ValueError(f"JSON node limit {maximum_nodes} exceeded")
        if depth > maximum_depth:
            raise ValueError(f"JSON depth limit {maximum_depth} exceeded")
        if isinstance(item, dict):
            identity = id(item)
            if identity in seen_containers:
                raise ValueError("JSON value contains a recursive object")
            seen_containers.add(identity)
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            identity = id(item)
            if identity in seen_containers:
                raise ValueError("JSON value contains a recursive array")
            seen_containers.add(identity)
            pending.extend((child, depth + 1) for child in item)
    return nodes, depth_seen


class SafeFileError(OSError):
    """A bounded public diagnostic for a release-tree identity/read failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _is_sparse(value: os.stat_result) -> bool:
    return value.st_size > 4096 and getattr(value, "st_blocks", 0) * 512 < value.st_size


class StageSnapshotLease:
    """Hold the cooperative root-directory flock for one complete inventory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(os.fspath(root.expanduser())))
        self.file_descriptor = -1
        self.identity: tuple[int, ...] | None = None

    def __enter__(self) -> StageSnapshotLease:
        try:
            before = os.stat(self.root, follow_symlinks=False)
        except OSError as exc:
            raise ValueError(f"cannot stat static build directory {self.root}: {exc}") from exc
        if stat.S_ISLNK(before.st_mode):
            raise SafeFileError("unsafe-tree-symlink", "release root is a symbolic link")
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError(f"static build path is not a directory: {self.root}")
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise SafeFileError("unsafe-tree-read-error", "O_NOFOLLOW is required for the snapshot lease")
        try:
            self.file_descriptor = os.open(
                self.root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | nofollow,
            )
            opened = os.fstat(self.file_descriptor)
            if not stat.S_ISDIR(opened.st_mode) or _file_identity(opened) != _file_identity(before):
                raise SafeFileError("unsafe-tree-identity-change", "release root changed while leasing")
            try:
                fcntl.flock(self.file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("static build directory snapshot lease is busy") from exc
            self.identity = _file_identity(opened)
            self.verify()
            return self
        except BaseException:
            self.release()
            raise

    def verify(self) -> None:
        if self.file_descriptor < 0 or self.identity is None:
            raise SafeFileError("unsafe-tree-read-error", "release snapshot lease is not held")
        try:
            descriptor_state = os.fstat(self.file_descriptor)
            path_state = os.stat(self.root, follow_symlinks=False)
        except OSError as exc:
            raise SafeFileError("unsafe-tree-identity-change", "release root changed while leased") from exc
        if (
            not stat.S_ISDIR(descriptor_state.st_mode)
            or _file_identity(descriptor_state) != self.identity
            or _file_identity(path_state) != self.identity
        ):
            raise SafeFileError("unsafe-tree-identity-change", "release root changed while leased")

    def release(self) -> None:
        if self.file_descriptor < 0:
            return
        try:
            fcntl.flock(self.file_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self.file_descriptor)
            self.file_descriptor = -1
            self.identity = None

    def __exit__(self, _exception_type, _exception, _traceback) -> None:
        self.release()


@contextmanager
def _safe_open_release_file(root: Path, path: Path, maximum: int):
    """Open one root-contained file through no-follow directory descriptors."""
    root = Path(os.path.abspath(os.fspath(root)))
    path = Path(os.path.abspath(os.fspath(path)))
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise SafeFileError("unsafe-tree-escaping-path", "file is outside the release root") from exc
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise SafeFileError("unsafe-tree-escaping-path", "file path is not root-contained")
    directory_fds: list[int] = []
    directory_records: list[tuple[int, str, os.stat_result, Path]] = []
    file_fd = -1
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise SafeFileError("unsafe-tree-read-error", "O_NOFOLLOW is required for release reads")
        root_before = os.stat(root, follow_symlinks=False)
        root_fd = os.open(root, directory_flags | nofollow)
        root_opened = os.fstat(root_fd)
        if not stat.S_ISDIR(root_opened.st_mode) or _file_identity(root_before) != _file_identity(root_opened):
            raise SafeFileError("unsafe-tree-identity-change", "release root changed while opening")
        directory_fds.append(root_fd)
        current_fd = root_fd
        expected_directory = root
        for part in relative.parts[:-1]:
            directory_before = os.stat(part, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISDIR(directory_before.st_mode):
                raise SafeFileError("unsafe-tree-symlink", "release path component is not a regular directory")
            next_fd = os.open(part, directory_flags | nofollow, dir_fd=current_fd)
            directory_opened = os.fstat(next_fd)
            if _file_identity(directory_before) != _file_identity(directory_opened):
                os.close(next_fd)
                raise SafeFileError("unsafe-tree-identity-change", "release directory changed while opening")
            expected_directory /= part
            directory_records.append((current_fd, part, directory_opened, expected_directory))
            directory_fds.append(next_fd)
            current_fd = next_fd
        name = relative.parts[-1]
        before = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise SafeFileError("unsafe-tree-special-file", "release entry is not a regular file")
        if before.st_nlink != 1:
            raise SafeFileError("unsafe-tree-hardlink", "release file must have exactly one link")
        if _is_sparse(before):
            raise SafeFileError("unsafe-tree-sparse-file", "sparse release files are forbidden")
        if before.st_size > maximum:
            raise SafeFileError("limit-exceeded", f"byte limit {maximum} exceeded")
        file_fd = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0) | nofollow,
            dir_fd=current_fd,
        )
        opened = os.fstat(file_fd)
        if _file_identity(opened) != _file_identity(before):
            raise SafeFileError("unsafe-tree-identity-change", "release file changed while opening")
        try:
            root_descriptor_path = Path(os.readlink(f"/proc/self/fd/{root_fd}"))
            directory_descriptor_paths = [
                Path(os.readlink(f"/proc/self/fd/{directory_fd}")) for directory_fd in directory_fds[1:]
            ]
            descriptor_path = Path(os.readlink(f"/proc/self/fd/{file_fd}"))
        except OSError as exc:
            raise SafeFileError("unsafe-tree-escaping-path", "cannot verify opened descriptor containment") from exc
        if (
            root_descriptor_path != root
            or directory_descriptor_paths != [record[3] for record in directory_records]
            or descriptor_path != path
        ):
            raise SafeFileError("unsafe-tree-escaping-path", "opened descriptor path is not exact and root-contained")
        try:
            yield file_fd, opened
        finally:
            after = os.fstat(file_fd)
            path_after = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
            root_after = os.stat(root, follow_symlinks=False)
            directory_changed = any(
                _file_identity(os.stat(part, dir_fd=parent_fd, follow_symlinks=False))
                != _file_identity(directory_opened)
                for parent_fd, part, directory_opened, _expected_path in directory_records
            )
            if (
                _file_identity(after) != _file_identity(opened)
                or after.st_nlink != 1
                or _file_identity(path_after) != _file_identity(opened)
                or _file_identity(root_after) != _file_identity(root_opened)
                or directory_changed
            ):
                raise SafeFileError("unsafe-tree-identity-change", "release file changed while reading")
    except SafeFileError:
        raise
    except OSError as exc:
        raise SafeFileError("unsafe-tree-read-error", "descriptor-bound release read failed") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)


def _safe_read_bytes_with_identity(root: Path, path: Path, maximum: int) -> tuple[bytes, tuple[int, ...]]:
    try:
        chunks: list[bytes] = []
        total = 0
        with _safe_open_release_file(root, path, maximum) as (file_fd, expected):
            while total < expected.st_size:
                chunk = os.read(file_fd, min(1024 * 1024, expected.st_size - total))
                if not chunk:
                    raise SafeFileError("unsafe-tree-identity-change", "release file truncated while reading")
                chunks.append(chunk)
                total += len(chunk)
            if os.read(file_fd, 1):
                raise SafeFileError("unsafe-tree-identity-change", "release file grew while reading")
        return b"".join(chunks), _file_identity(expected)
    except MemoryError as exc:
        raise SafeFileError("limit-exceeded", "bounded release read exhausted memory") from exc


def _safe_read_bytes(root: Path, path: Path, maximum: int) -> bytes:
    value, _identity = _safe_read_bytes_with_identity(root, path, maximum)
    return value


def _safe_read_prefix(root: Path, path: Path, maximum: int, prefix_bytes: int = 64 * 1024) -> tuple[bytes, int]:
    try:
        with _safe_open_release_file(root, path, maximum) as (file_fd, expected):
            value = os.read(file_fd, min(expected.st_size, prefix_bytes))
        return value, expected.st_size
    except MemoryError as exc:
        raise SafeFileError("limit-exceeded", "bounded release prefix read exhausted memory") from exc


def _safe_read_prefix_and_sha256(
    root: Path,
    path: Path,
    maximum: int,
    prefix_bytes: int = 64 * 1024,
) -> tuple[bytes, int, str]:
    """Capture a media prefix and digest from one stable, bounded descriptor."""

    try:
        digest = hashlib.sha256()
        prefix = bytearray()
        with _safe_open_release_file(root, path, maximum) as (file_fd, expected):
            remaining = expected.st_size
            while remaining:
                chunk = os.read(file_fd, min(1024 * 1024, remaining))
                if not chunk:
                    raise SafeFileError("unsafe-tree-identity-change", "release file truncated while reading")
                digest.update(chunk)
                if len(prefix) < prefix_bytes:
                    prefix.extend(chunk[: prefix_bytes - len(prefix)])
                remaining -= len(chunk)
            if os.read(file_fd, 1):
                raise SafeFileError("unsafe-tree-identity-change", "release file grew while reading")
        return bytes(prefix), expected.st_size, digest.hexdigest()
    except MemoryError as exc:
        raise SafeFileError("limit-exceeded", "bounded release media read exhausted memory") from exc


def _safe_sha256_file_with_identity(
    root: Path,
    path: Path,
    maximum: int,
) -> tuple[int, str, tuple[int, ...]]:
    digest = hashlib.sha256()
    with _safe_open_release_file(root, path, maximum) as (file_fd, expected):
        remaining = expected.st_size
        while remaining:
            chunk = os.read(file_fd, min(1024 * 1024, remaining))
            if not chunk:
                raise SafeFileError("unsafe-tree-identity-change", "release file truncated while hashing")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(file_fd, 1):
            raise SafeFileError("unsafe-tree-identity-change", "release file grew while hashing")
    return expected.st_size, digest.hexdigest(), _file_identity(expected)


def _safe_sha256_file(root: Path, path: Path, maximum: int) -> tuple[int, str]:
    size, digest, _identity = _safe_sha256_file_with_identity(root, path, maximum)
    return size, digest


@dataclass(frozen=True)
class StageFileCapture:
    """One exact release-file observation retained until the stage boundary."""

    path: Path
    maximum: int
    size: int
    sha256: str
    identity: tuple[int, ...]


@dataclass(frozen=True)
class StageTreeEntry:
    """Exact no-follow metadata for one preflight tree entry."""

    kind: str
    mode: int
    identity: tuple[int, ...]
    link_count: int
    size: int
    allocated_bytes: int
    sparse: bool


@dataclass(frozen=True)
class StageTreeAggregate:
    """Bounded aggregate state captured by the complete tree preflight."""

    entries: int
    directories: int
    files: int
    html_files: int
    assets: int
    bytes: int


@dataclass(frozen=True)
class StageTreeSnapshot:
    """The exact preflight membership and metadata bound to final evidence."""

    entries: dict[str, StageTreeEntry]
    aggregate: StageTreeAggregate


def _stage_tree_kind(value: os.stat_result) -> str:
    mode = value.st_mode
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    return "special"


def _stage_tree_entry(value: os.stat_result) -> StageTreeEntry:
    return StageTreeEntry(
        kind=_stage_tree_kind(value),
        mode=value.st_mode,
        identity=_file_identity(value),
        link_count=value.st_nlink,
        size=value.st_size,
        allocated_bytes=max(0, getattr(value, "st_blocks", 0)) * 512,
        sparse=_is_sparse(value),
    )


class StageBoundaryVerifier:
    """Bind evidence to a cooperatively immutable tree bracketed by exact scans.

    No userspace re-enumeration can prevent a hostile writer from mutating the
    tree after the final scan or after this verifier returns.  Callers needing
    that stronger boundary must hold a filesystem snapshot or external lease.
    """

    def __init__(
        self,
        root: Path,
        snapshot: StageTreeSnapshot,
        limits: dict[str, int],
    ) -> None:
        self.root = Path(os.path.abspath(os.fspath(root)))
        self.snapshot = snapshot
        self.limits = limits
        self._captures: dict[Path, StageFileCapture] = {}

    def _record(
        self,
        path: Path,
        *,
        maximum: int,
        size: int,
        digest: str,
        identity: tuple[int, ...],
    ) -> None:
        absolute = Path(os.path.abspath(os.fspath(path)))
        capture = StageFileCapture(
            path=absolute,
            maximum=maximum,
            size=size,
            sha256=digest,
            identity=identity,
        )
        existing = self._captures.get(absolute)
        if existing is not None:
            if existing.size != size or existing.sha256 != digest or existing.identity != identity:
                raise SafeFileError(
                    "unsafe-tree-identity-change",
                    "release file changed between evidence reads",
                )
            capture = StageFileCapture(
                path=absolute,
                maximum=min(existing.maximum, maximum),
                size=size,
                sha256=digest,
                identity=identity,
            )
        self._captures[absolute] = capture

    def read_bytes_with_identity(
        self,
        path: Path,
        maximum: int,
    ) -> tuple[bytes, tuple[int, ...]]:
        value, identity = _safe_read_bytes_with_identity(self.root, path, maximum)
        self._record(
            path,
            maximum=maximum,
            size=len(value),
            digest=hashlib.sha256(value).hexdigest(),
            identity=identity,
        )
        return value, identity

    def read_bytes(self, path: Path, maximum: int) -> bytes:
        value, _identity = self.read_bytes_with_identity(path, maximum)
        return value

    def sha256_file(self, path: Path, maximum: int) -> tuple[int, str]:
        size, digest, identity = _safe_sha256_file_with_identity(self.root, path, maximum)
        self._record(
            path,
            maximum=maximum,
            size=size,
            digest=digest,
            identity=identity,
        )
        return size, digest

    @staticmethod
    def _tree_drift_finding() -> dict[str, Any]:
        return {
            "code": "unsafe-tree-read-error",
            "detail": "release tree changed before the stage boundary",
            "path": "/",
        }

    @staticmethod
    def _tree_limit_finding(detail: str) -> dict[str, Any]:
        return {
            "code": "limit-exceeded",
            "detail": detail,
            "path": "/",
            "resource": "stage-boundary",
        }

    def _verify_tree(self) -> list[dict[str, Any]]:
        """Re-enumerate the preflight tree through bounded no-follow descriptors."""

        findings: list[dict[str, Any]] = []
        remaining = dict(self.snapshot.entries)
        changed = False
        halted = False
        total_entries = 0
        total_directories = 1
        total_files = 0
        total_html_files = 0
        total_assets = 0
        total_bytes = 0

        def add_limit(detail: str) -> None:
            nonlocal halted
            finding = self._tree_limit_finding(detail)
            if finding not in findings:
                findings.append(finding)
            halted = True

        def compare(relative: str, value: os.stat_result) -> None:
            nonlocal changed
            expected = remaining.pop(relative, None)
            if expected is None or _stage_tree_entry(value) != expected:
                changed = True

        def scan_directory(directory_fd: int, parts: tuple[str, ...]) -> None:
            nonlocal changed
            nonlocal halted
            nonlocal total_assets
            nonlocal total_bytes
            nonlocal total_directories
            nonlocal total_entries
            nonlocal total_files
            nonlocal total_html_files
            if halted:
                return
            names: list[str] = []
            try:
                with os.scandir(directory_fd) as iterator:
                    for entry in iterator:
                        if len(names) >= self.limits["directory_entries"]:
                            add_limit("stage boundary per-directory entry limit exceeded")
                            return
                        names.append(entry.name)
            except OSError:
                changed = True
                return
            try:
                names.sort()
            except (MemoryError, UnicodeError):
                add_limit("stage boundary directory inventory exhausted its bound")
                return
            for name in names:
                if halted:
                    return
                total_entries += 1
                if total_entries > self.limits["tree_entries"]:
                    add_limit("stage boundary tree entry limit exceeded")
                    return
                relative_parts = (*parts, name)
                try:
                    relative = "/".join(relative_parts)
                    if len(relative.encode("utf-8")) > self.limits["path_bytes"]:
                        add_limit("stage boundary path byte limit exceeded")
                        return
                    value = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except (OSError, UnicodeError):
                    changed = True
                    continue
                compare(relative, value)
                kind = _stage_tree_kind(value)
                if kind == "directory":
                    total_directories += 1
                    if len(relative_parts) > self.limits["directory_depth"]:
                        add_limit("stage boundary directory depth limit exceeded")
                        return
                    if total_directories > self.limits["tree_directories"]:
                        add_limit("stage boundary directory count limit exceeded")
                        return
                    child_fd = -1
                    try:
                        nofollow = getattr(os, "O_NOFOLLOW", None)
                        if nofollow is None:
                            changed = True
                            continue
                        child_fd = os.open(
                            name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | nofollow,
                            dir_fd=directory_fd,
                        )
                        opened = os.fstat(child_fd)
                        if _stage_tree_entry(opened) != _stage_tree_entry(value):
                            changed = True
                        scan_directory(child_fd, relative_parts)
                        after = os.fstat(child_fd)
                        path_after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if _stage_tree_entry(after) != _stage_tree_entry(opened) or _stage_tree_entry(
                            path_after
                        ) != _stage_tree_entry(opened):
                            changed = True
                    except OSError:
                        changed = True
                    finally:
                        if child_fd >= 0:
                            os.close(child_fd)
                elif kind == "file":
                    total_files += 1
                    total_bytes += value.st_size
                    if value.st_size > self.limits["asset_bytes"]:
                        add_limit("stage boundary asset byte limit exceeded")
                        return
                    if total_bytes > self.limits["tree_bytes"]:
                        add_limit("stage boundary aggregate byte limit exceeded")
                        return
                    if name.lower().endswith(".html"):
                        total_html_files += 1
                    else:
                        total_assets += 1
                        if total_assets > self.limits["asset_count"]:
                            add_limit("stage boundary asset count limit exceeded")
                            return

        root_fd = -1
        try:
            nofollow = getattr(os, "O_NOFOLLOW", None)
            if nofollow is None:
                return [self._tree_drift_finding()]
            root_before = os.stat(self.root, follow_symlinks=False)
            root_fd = os.open(
                self.root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | nofollow,
            )
            root_opened = os.fstat(root_fd)
            compare("", root_opened)
            if _stage_tree_entry(root_before) != _stage_tree_entry(root_opened):
                changed = True
            scan_directory(root_fd, ())
            root_after = os.fstat(root_fd)
            path_after = os.stat(self.root, follow_symlinks=False)
            if _stage_tree_entry(root_after) != _stage_tree_entry(root_opened) or _stage_tree_entry(
                path_after
            ) != _stage_tree_entry(root_opened):
                changed = True
        except (MemoryError, OSError, RecursionError, UnicodeError):
            changed = True
        finally:
            if root_fd >= 0:
                os.close(root_fd)

        aggregate = StageTreeAggregate(
            entries=total_entries,
            directories=total_directories,
            files=total_files,
            html_files=total_html_files,
            assets=total_assets,
            bytes=total_bytes,
        )
        if remaining or aggregate != self.snapshot.aggregate:
            changed = True
        if changed:
            findings.append(self._tree_drift_finding())
        return sorted({canonical_json(item): item for item in findings}.values(), key=canonical_json)

    def verify(self) -> list[dict[str, Any]]:
        """Bracket every capture rehash with exact no-follow tree scans."""

        def verify_tree_safely() -> list[dict[str, Any]]:
            try:
                return self._verify_tree()
            except MemoryError:
                return [self._tree_limit_finding("stage boundary tree inventory exhausted its bound")]
            except (OSError, RecursionError, UnicodeError):
                return [self._tree_drift_finding()]

        findings = verify_tree_safely()
        for path, capture in sorted(self._captures.items(), key=lambda item: os.fspath(item[0])):
            try:
                size, digest, identity = _safe_sha256_file_with_identity(
                    self.root,
                    path,
                    capture.maximum,
                )
                if size != capture.size or digest != capture.sha256 or identity != capture.identity:
                    raise SafeFileError(
                        "unsafe-tree-identity-change",
                        "release file changed before the evidence boundary",
                    )
            except MemoryError:
                findings.append(
                    {
                        "code": "limit-exceeded",
                        "detail": "stage boundary content rehash exhausted its bound",
                        "path": "/",
                        "resource": "stage-boundary",
                    }
                )
            except SafeFileError as exc:
                try:
                    relative = path.relative_to(self.root).as_posix()
                except ValueError:
                    relative = ""
                if exc.code == "limit-exceeded":
                    findings.append(
                        {
                            "code": "limit-exceeded",
                            "detail": str(exc),
                            "path": f"/{relative}" if relative else "/",
                            "resource": "stage-boundary",
                        }
                    )
                else:
                    findings.append(
                        {
                            "code": "unsafe-tree-read-error",
                            "detail": "release evidence changed before the stage boundary",
                            "path": f"/{relative}" if relative else "/",
                        }
                    )
        findings.extend(verify_tree_safely())
        return sorted({canonical_json(item): item for item in findings}.values(), key=canonical_json)


def _tree_preflight(
    root: Path,
    limits: dict[str, int] | None = None,
) -> tuple[Path, list[Path], list[dict[str, Any]], StageTreeSnapshot | None]:
    """Inventory a build tree without following links or reading file content."""
    absolute_root = Path(os.path.abspath(os.fspath(root.expanduser())))
    try:
        root_stat = absolute_root.lstat()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot stat static build directory {absolute_root}: {exc}")
    if stat.S_ISLNK(root_stat.st_mode):
        return absolute_root, [], [{"code": "unsafe-tree-symlink", "path": "/"}], None
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError(f"static build path is not a directory: {absolute_root}")
    try:
        if absolute_root.resolve() != absolute_root:
            return absolute_root, [], [{"code": "unsafe-tree-escaping-path", "path": "/"}], None
    except (OSError, RuntimeError) as exc:
        return absolute_root, [], [{"code": "unsafe-tree-stat-error", "path": "/", "detail": str(exc)}], None

    files: list[Path] = []
    findings: list[dict[str, Any]] = []
    pending = [absolute_root]
    total_bytes = 0
    total_directories = 1
    total_entries = 0
    asset_count = 0
    html_count = 0
    tree_entries: dict[str, StageTreeEntry] = {"": _stage_tree_entry(root_stat)}

    def limit_finding(
        detail: str,
        path: str,
        resource: str = "tree",
    ) -> tuple[Path, list[Path], list[dict[str, Any]], StageTreeSnapshot | None]:
        return (
            absolute_root,
            [],
            [{"code": "limit-exceeded", "detail": detail, "path": path, "resource": resource}],
            None,
        )

    while pending:
        directory = pending.pop()
        try:
            entries: list[os.DirEntry[str]] = []
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    total_entries += 1
                    if limits and total_entries > limits["tree_entries"]:
                        return limit_finding(f"tree entry limit {limits['tree_entries']} exceeded", "/")
                    if limits and len(entries) >= limits["directory_entries"]:
                        relative = directory.relative_to(absolute_root).as_posix() or "."
                        return limit_finding(
                            f"per-directory entry limit {limits['directory_entries']} exceeded",
                            f"/{relative}",
                        )
                    entries.append(entry)
            entries.sort(key=lambda entry: entry.name)
        except OSError as exc:
            relative = directory.relative_to(absolute_root).as_posix() or "."
            findings.append({"code": "unsafe-tree-unreadable-directory", "path": f"/{relative}", "detail": str(exc)})
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                relative_path = path.relative_to(absolute_root)
            except ValueError:
                findings.append({"code": "unsafe-tree-escaping-path", "path": path.name})
                continue
            relative = relative_path.as_posix()
            if limits and len(relative.encode("utf-8")) > limits["path_bytes"]:
                return limit_finding(f"path byte limit {limits['path_bytes']} exceeded", "/")
            if relative_path.is_absolute() or ".." in relative_path.parts:
                findings.append({"code": "unsafe-tree-escaping-path", "path": f"/{relative}"})
                continue
            try:
                entry_stat = entry.stat(follow_symlinks=False)
                mode = entry_stat.st_mode
            except OSError as exc:
                findings.append({"code": "unsafe-tree-stat-error", "path": f"/{relative}", "detail": str(exc)})
                continue
            tree_entries[relative] = _stage_tree_entry(entry_stat)
            if stat.S_ISLNK(mode):
                try:
                    path.resolve(strict=False).relative_to(absolute_root.resolve())
                except (OSError, RuntimeError, ValueError):
                    code = "unsafe-tree-escaping-path"
                else:
                    code = "unsafe-tree-symlink"
                findings.append({"code": code, "path": f"/{relative}"})
            elif stat.S_ISDIR(mode):
                if path.name.lower().endswith(".html"):
                    findings.append({"code": "unsafe-tree-html-directory", "path": f"/{relative}"})
                else:
                    if limits and len(relative_path.parts) > limits["directory_depth"]:
                        return limit_finding(
                            f"directory depth limit {limits['directory_depth']} exceeded",
                            f"/{relative}",
                        )
                    total_directories += 1
                    if limits and total_directories > limits["tree_directories"]:
                        return limit_finding(
                            f"directory count limit {limits['tree_directories']} exceeded",
                            "/",
                        )
                    pending.append(path)
            elif stat.S_ISREG(mode):
                if entry_stat.st_nlink != 1:
                    findings.append({"code": "unsafe-tree-hardlink", "path": f"/{relative}"})
                elif _is_sparse(entry_stat):
                    findings.append({"code": "unsafe-tree-sparse-file", "path": f"/{relative}"})
                elif limits and entry_stat.st_size > limits["asset_bytes"]:
                    findings.append(
                        {
                            "code": "limit-exceeded",
                            "detail": f"byte limit {limits['asset_bytes']} exceeded",
                            "path": f"/{relative}",
                            "resource": "asset",
                        }
                    )
                else:
                    total_bytes += entry_stat.st_size
                    if limits and total_bytes > limits["tree_bytes"]:
                        return limit_finding(f"aggregate byte limit {limits['tree_bytes']} exceeded", "/")
                    if path.suffix.lower() != ".html":
                        asset_count += 1
                        if limits and asset_count > limits["asset_count"]:
                            return limit_finding(
                                f"asset count limit {limits['asset_count']} exceeded",
                                "/",
                                "asset",
                            )
                    else:
                        html_count += 1
                    files.append(path)
            else:
                findings.append({"code": "unsafe-tree-special-file", "path": f"/{relative}"})
    return (
        absolute_root,
        sorted(files, key=lambda path: path.relative_to(absolute_root).as_posix()),
        sorted(findings, key=canonical_json),
        StageTreeSnapshot(
            entries=tree_entries,
            aggregate=StageTreeAggregate(
                entries=total_entries,
                directories=total_directories,
                files=len(files),
                html_files=html_count,
                assets=asset_count,
                bytes=total_bytes,
            ),
        ),
    )


def _empty_manifest(
    origin: str,
    findings: list[dict[str, Any]],
    limits: dict[str, int],
    source_snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "source_snapshot": _json_deep_copy(source_snapshot),
        "origin": origin.rstrip("/"),
        "limits": limits,
        "routes": {},
        "aliases": {},
        "assets": {},
        "hls": {"policy": _authority_copy(_HLS_POLICY_AUTHORITY_JSON), "playlists": {}, "roots": []},
        "features": {
            name: {
                "present": False,
                "verification": "structural-only",
                "evidence": {
                    "controls": [],
                    "documents": [],
                    "index_assets": [],
                    "index_documents": [],
                    "markup": [],
                    "runtime_assets": [],
                    "routes": {},
                    "suppressed_routes": {},
                },
            }
            for name in FEATURE_NAMES
        },
        "integrity": {"missing_assets": [], "findings": findings},
        "verification_scope": _verification_scope_for(source_snapshot),
    }


@dataclass
class TableCapture:
    caption_parts: list[str] = field(default_factory=list)
    rows: list[list[dict[str, str]]] = field(default_factory=list)
    row: list[dict[str, str]] | None = None
    cell_kind: str | None = None
    cell_parts: list[str] = field(default_factory=list)
    in_caption: bool = False


@dataclass
class ElementFrame:
    tag: str
    visual_states: tuple[str, ...]
    interactivity_states: tuple[str, ...]
    inherited_interactivity_states: tuple[str, ...] = ()
    own_interactivity_states: tuple[str, ...] = ()
    closed_details: bool = False
    first_summary_seen: bool = False
    disabled_fieldset: bool = False
    first_legend_seen: bool = False
    disabled_fieldset_depth: int = 0
    inert_context: bool = False
    accessibility_states: tuple[str, ...] = ()


@dataclass
class RevealableCapture:
    """Semantic content hidden only until a native disclosure control opens."""

    tag: str
    depth: int
    location: str
    collector: ContentCollector = field(default_factory=lambda: ContentCollector())
    suppressed_depth: int = 0


@dataclass
class ContentCollector:
    """Capture semantic content for one ``article``, ``main``, or body region."""

    text_parts: list[str] = field(default_factory=list)
    headings: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    media: list[dict[str, Any]] = field(default_factory=list)
    grafana: list[dict[str, str]] = field(default_factory=list)
    asset_refs: set[tuple[str, str]] = field(default_factory=set)
    fragment_targets: set[str] = field(default_factory=set)
    heading_level: int | None = None
    heading_parts: list[str] = field(default_factory=list)
    active_link: dict[str, Any] | None = None
    table: TableCapture | None = None

    def start(
        self,
        tag: str,
        attrs: dict[str, str],
        *,
        route: str,
        origin: str,
        base_url: str | None = None,
        base_target: str = "",
    ) -> None:
        if tag in TEXT_BOUNDARY_TAGS:
            self.text_parts.append(" ")
        element_id = html.unescape(attrs.get("id", "")).strip()
        legacy_name = html.unescape(attrs.get("name", "")).strip() if tag == "a" else ""
        if element_id:
            self.fragment_targets.add(element_id)
        if legacy_name:
            self.fragment_targets.add(legacy_name)
        if re.fullmatch(r"h[1-6]", tag):
            self.heading_level = int(tag[1])
            self.heading_parts = []

        decorative_heading_anchor = (
            tag == "a"
            and attrs.get("role", "").strip().lower() == "anchor"
            and attrs.get("aria-hidden", "").strip().lower() == "true"
        )
        if tag in {"a", "area"} and attrs.get("href") and not decorative_heading_anchor:
            href = normalize_reference(attrs["href"], route=route, origin=origin, route_like=True, base_url=base_url)
            active_link = {
                "href": href,
                "text_parts": [attrs.get("alt", "")] if tag == "area" else [],
                "download_attr": attrs.get("download", "") if "download" in attrs else None,
                "rel": sorted(set(attrs.get("rel", "").lower().split())),
                "target": attrs.get("target", "").strip() if "target" in attrs else base_target,
                "referrerpolicy": attrs.get("referrerpolicy", "").strip().lower(),
                "hreflang": attrs.get("hreflang", "").strip().lower(),
                "type": attrs.get("type", "").strip().lower(),
            }
            if tag == "area":
                self._finish_link(active_link)
            else:
                self.active_link = active_link

        if tag == "table":
            self.table = TableCapture()
        elif self.table is not None and tag == "caption":
            self.table.in_caption = True
        elif self.table is not None and tag == "tr":
            self.table.row = []
        elif self.table is not None and tag in {"td", "th"}:
            self.table.cell_kind = tag
            self.table.cell_parts = []

        self._capture_media(tag, attrs, route=route, origin=origin, base_url=base_url)

    def data(self, value: str) -> None:
        self.text_parts.append(value)
        if self.heading_level is not None:
            self.heading_parts.append(value)
        if self.active_link is not None:
            self.active_link["text_parts"].append(value)
        if self.table is not None:
            if self.table.cell_kind is not None:
                self.table.cell_parts.append(value)
            elif self.table.in_caption:
                self.table.caption_parts.append(value)

    def end(self, tag: str) -> None:
        if self.heading_level is not None and tag == f"h{self.heading_level}":
            self.headings.append({"level": self.heading_level, "text": normalize_text("".join(self.heading_parts))})
            self.heading_level = None
            self.heading_parts = []

        if tag == "a" and self.active_link is not None:
            self._finish_link(self.active_link)
            self.active_link = None

        if self.table is not None and tag in {"td", "th"} and self.table.cell_kind == tag:
            if self.table.row is None:
                self.table.row = []
            self.table.row.append({"kind": tag, "text": normalize_text("".join(self.table.cell_parts))})
            self.table.cell_kind = None
            self.table.cell_parts = []
        elif self.table is not None and tag == "tr":
            if self.table.row is not None:
                self.table.rows.append(self.table.row)
            self.table.row = None
        elif self.table is not None and tag == "caption":
            self.table.in_caption = False
        elif self.table is not None and tag == "table":
            self.tables.append(
                {
                    "caption": normalize_text("".join(self.table.caption_parts)),
                    "rows": self.table.rows,
                }
            )
            self.table = None
        if tag in TEXT_BOUNDARY_TAGS:
            self.text_parts.append(" ")

    def _finish_link(self, active_link: dict[str, Any]) -> None:
        href = active_link["href"]
        text = normalize_text("".join(active_link["text_parts"]))
        download_attr = active_link["download_attr"]
        download = download_attr is not None or Path(urlsplit(href).path).suffix.lower() in DOWNLOAD_SUFFIXES
        self.links.append(
            {
                "href": href,
                "text": text,
                "download": download,
                "download_filename": download_attr or "" if download_attr is not None else "",
                "rel": active_link["rel"],
                "target": active_link["target"],
                "referrerpolicy": active_link["referrerpolicy"],
                "hreflang": active_link["hreflang"],
                "type": active_link["type"],
            }
        )
        if download:
            self.downloads.append({"href": href, "text": text, "filename": download_attr or ""})
            if is_local_reference(href):
                self.asset_refs.add((urlsplit(href).path, "download"))

    def _capture_media(
        self,
        tag: str,
        attrs: dict[str, str],
        *,
        route: str,
        origin: str,
        base_url: str | None = None,
    ) -> None:
        grafana = grafana_occurrence(tag, attrs, route=route, origin=origin, base_url=base_url)
        if grafana is not None:
            self.grafana.append(grafana)
            primary_source = grafana["live_url"] or grafana["fallback_url"]
            item: dict[str, Any] = {"kind": "iframe", "src": primary_source}
            if attrs.get("title"):
                item["title"] = normalize_text(attrs["title"])
            self.media.append(item)
            for source in (grafana["live_url"], grafana["fallback_url"]):
                if source and is_local_reference(source):
                    self.asset_refs.add((urlsplit(source).path, "media"))
            return

        candidates: list[tuple[str, str]] = []
        if tag in {"audio", "embed", "img", "object", "source", "track", "video"}:
            attribute = "data" if tag == "object" else "src"
            if attrs.get(attribute):
                candidates.append((attribute, attrs[attribute]))
            if tag == "img" and attrs.get("data-src"):
                candidates.append(("data-src", attrs["data-src"]))
        if tag == "video" and attrs.get("poster"):
            candidates.append(("poster", attrs["poster"]))
        if tag in {"img", "source"} and attrs.get("srcset"):
            for source, descriptor in _parse_srcset(attrs["srcset"]):
                if source:
                    candidates.append((f"srcset:{descriptor}", source))
        for attribute in GRAFANA_SOURCE_ATTRIBUTES[:-1]:
            if attrs.get(attribute):
                candidates.append((attribute, attrs[attribute]))
        if tag == "iframe" and attrs.get("src"):
            candidates.append(("src", attrs["src"]))

        # The same URL can be selected through materially different browser
        # paths (for example ``src`` plus two ``srcset`` descriptors).  Preserve
        # each source role while still collapsing an exact duplicate occurrence.
        seen: set[tuple[str, str]] = set()
        for attribute, raw_source in candidates:
            source = normalize_reference(raw_source, route=route, origin=origin, base_url=base_url)
            occurrence = (source, attribute)
            if not source or occurrence in seen or source.startswith(("data:", "javascript:")):
                continue
            seen.add(occurrence)
            media_kind = "iframe" if attribute in {"data-iframe-src", "data-live-src"} else tag
            item: dict[str, Any] = {"kind": media_kind, "src": source}
            if attribute not in {"data-iframe-src", "data-live-src", "src"}:
                item["source_attribute"] = attribute
            if tag == "img":
                item["alt"] = normalize_text(attrs.get("alt", ""))
            if attrs.get("title"):
                item["title"] = normalize_text(attrs["title"])
            if attrs.get("sizes"):
                item["sizes"] = attrs["sizes"].strip()
            if attrs.get("media"):
                item["media"] = attrs["media"].strip()
            if attrs.get("type"):
                item["type"] = attrs["type"].strip().lower()
            self.media.append(item)
            if is_local_reference(source):
                self.asset_refs.add((urlsplit(source).path, "media"))


def _private_digest(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _bounded_grafana_query_pairs(query: str) -> tuple[list[tuple[str, str]], bool]:
    try:
        if len(query.encode()) > GRAFANA_MAX_QUERY_BYTES:
            return [], False
        pairs = parse_qsl(query, keep_blank_values=True)
    except (MemoryError, UnicodeError, ValueError):
        return [], False
    within_limits = len(pairs) <= GRAFANA_MAX_QUERY_PAIRS
    pairs = pairs[:GRAFANA_MAX_QUERY_PAIRS]
    if any(
        len(key.encode()) > GRAFANA_MAX_QUERY_KEY_BYTES or len(value.encode()) > GRAFANA_MAX_QUERY_VALUE_BYTES
        for key, value in pairs
    ):
        within_limits = False
    return pairs, within_limits


def _grafana_query_evidence(query: str) -> tuple[str, bool]:
    rendered: list[str] = []
    pairs, within_limits = _bounded_grafana_query_pairs(query)
    if not within_limits and not pairs:
        return f"overflow={_private_digest(query)}", False
    for key, value in pairs:
        key_bytes = key.encode()
        if HLS_CREDENTIAL_QUERY_RE.search(key) or len(key_bytes) > GRAFANA_MAX_QUERY_KEY_BYTES:
            safe_key = f"protected-{hashlib.sha256(key_bytes).hexdigest()}"
        else:
            safe_key = _normalize_percent_component(key, safe="-._~")
        protected_value = value if SHA256_RE.fullmatch(value) else _private_digest(value)
        rendered.append(f"{safe_key}={protected_value}")
    return "&".join(rendered), within_limits


def _sanitize_grafana_source(source: str) -> tuple[str, bool]:
    parts = urlsplit(source)
    query, within_limits = _grafana_query_evidence(parts.query)
    # Never retain URL user-info. Grafana query values are private by default:
    # the manifest carries only their one-way digest while preserving key,
    # order, and multiplicity as parity evidence.
    hostname = parts.hostname or ""
    try:
        port = parts.port
    except ValueError:
        port = None
        within_limits = False
    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    scheme = parts.scheme.lower()
    active = urlsplit(GRAFANA_ALLOWED_ORIGIN)
    exact_active_origin = scheme == active.scheme and hostname == active.hostname and port in {None, 443}
    if netloc and not exact_active_origin and hostname != "redacted.invalid":
        scheme = "https"
        netloc = "redacted.invalid"
    return urlunsplit((scheme, netloc.lower(), parts.path, query, "")), within_limits


def _grafana_live_source_status(raw_source: str, normalized: str, *, query_within_limits: bool) -> str:
    raw_parts = urlsplit(raw_source)
    parts = urlsplit(normalized)
    try:
        raw_port = raw_parts.port
    except ValueError:
        return "invalid-live-origin"
    if (
        raw_parts.scheme.lower() != urlsplit(GRAFANA_ALLOWED_ORIGIN).scheme
        or raw_parts.hostname != urlsplit(GRAFANA_ALLOWED_ORIGIN).hostname
        or raw_port not in {None, 443}
        or raw_parts.username is not None
        or raw_parts.password is not None
        or raw_parts.fragment
        or parts.scheme.lower() != urlsplit(GRAFANA_ALLOWED_ORIGIN).scheme
        or parts.hostname != urlsplit(GRAFANA_ALLOWED_ORIGIN).hostname
    ):
        return "invalid-live-origin"
    if not query_within_limits:
        return "invalid-live-query-limits"
    query_pairs, _bounded = _bounded_grafana_query_pairs(raw_parts.query)
    if any(HLS_CREDENTIAL_QUERY_RE.search(key) for key, _value in query_pairs):
        return "invalid-live-credential-query"
    target = _grafana_url_target(normalized)
    if target is None or "/render/" in parts.path.lower():
        return "invalid-live-target"
    return "active-live"


def _grafana_fallback_source_status(raw_source: str, normalized: str) -> str:
    raw_parts = urlsplit(raw_source)
    normalized_parts = urlsplit(normalized)
    decoded_segments = unquote(raw_parts.path).replace("\\", "/").split("/")
    release_local_image = (
        not raw_parts.scheme
        and not raw_parts.netloc
        and not raw_source.startswith("//")
        and "\\" not in raw_parts.path
        and ".." not in decoded_segments
        and not raw_parts.query
        and not raw_parts.fragment
        and not normalized_parts.scheme
        and not normalized_parts.netloc
        and bool(normalized_parts.path)
        and not normalized_parts.query
        and not normalized_parts.fragment
        and Path(normalized_parts.path).suffix.lower() in IMAGE_MIME_BY_SUFFIX
    )
    return "release-image" if release_local_image else "invalid-fallback"


def _grafana_url_target(source: str) -> dict[str, Any] | None:
    parts = urlsplit(source)
    if len(parts.path.encode()) > GRAFANA_MAX_PATH_BYTES:
        return None
    match = GRAFANA_PATH_RE.search(parts.path)
    if not match:
        return None
    uid = unquote(match.group(1))
    if not uid or len(uid.encode()) > GRAFANA_MAX_UID_BYTES or not re.fullmatch(r"[A-Za-z0-9_-]+", uid):
        return None
    panel_id = ""
    view_panel = ""
    variables: dict[str, list[str]] = {}
    time_range: dict[str, str] = {}
    query: dict[str, list[str]] = {}
    pairs, _within_limits = _bounded_grafana_query_pairs(parts.query)
    for key, value in pairs:
        lowered = key.lower()
        protected_value = value if SHA256_RE.fullmatch(value) else _private_digest(value)
        query.setdefault(key, []).append(protected_value)
        if lowered == "panelid":
            panel_id = protected_value
        elif lowered == "viewpanel":
            view_panel = protected_value
        elif lowered.startswith("var-"):
            variables.setdefault(key, []).append(protected_value)
        elif lowered in {"from", "time", "time.window", "to"}:
            time_range[lowered] = protected_value
    return {
        "uid": uid,
        "panel_id": panel_id,
        "view_panel": view_panel,
        "query": {key: values for key, values in query.items()},
        "variables": {key: values for key, values in variables.items()},
        "time_range": dict(sorted(time_range.items())),
    }


def _grafana_evidence_conflicts(
    sources: dict[str, str],
    source_roles: dict[str, str],
    source_status: dict[str, str],
) -> list[str]:
    live_sources = [
        sources[attribute]
        for attribute in sources
        if source_roles[attribute] == "live" and source_status[attribute] == "active-live"
    ]
    fallback_sources = [
        sources[attribute]
        for attribute in sources
        if source_roles[attribute] == "fallback" and source_status[attribute] == "release-image"
    ]
    comparable_targets = [target for source in live_sources if (target := _grafana_url_target(source)) is not None]
    conflicts: list[str] = []
    if len({item["uid"] for item in comparable_targets}) > 1:
        conflicts.append("uid")
    if len({item["panel_id"] or item["view_panel"] for item in comparable_targets}) > 1:
        conflicts.append("panel")
    if len({canonical_json(item["variables"]) for item in comparable_targets}) > 1:
        conflicts.append("variables")
    if len({canonical_json(item["time_range"]) for item in comparable_targets}) > 1:
        conflicts.append("time_range")
    if len(set(live_sources)) > 1:
        conflicts.append("live_url")
    if len(set(fallback_sources)) > 1:
        conflicts.append("fallback_url")
    if set(live_sources) & set(fallback_sources):
        conflicts.append("role_url")
    if any(
        source_roles[attribute] == "live" and "/render/" in urlsplit(source).path.lower()
        for attribute, source in sources.items()
    ):
        conflicts.append("live_render")
    invalid_statuses = set(source_status.values())
    for status, conflict in (
        ("invalid-fallback", "fallback_not_release_image"),
        ("invalid-live-origin", "live_origin"),
        ("invalid-live-query-limits", "live_query_limits"),
        ("invalid-live-credential-query", "live_credential_query"),
        ("invalid-live-target", "live_target"),
    ):
        if status in invalid_statuses:
            conflicts.append(conflict)
    return conflicts


def grafana_occurrence(
    tag: str,
    attrs: dict[str, str],
    *,
    route: str,
    origin: str,
    location: str = "content",
    visibility_states: tuple[str, ...] = (),
    base_url: str | None = None,
) -> dict[str, Any] | None:
    """Return one normalized occurrence for a possibly multi-source embed."""
    sources: list[tuple[str, str, str, str, bool]] = []
    for attribute in GRAFANA_SOURCE_ATTRIBUTES:
        if attrs.get(attribute):
            raw_source = html.unescape(attrs[attribute].strip())
            normalized = normalize_reference(raw_source, route=route, origin=origin, base_url=base_url)
            sanitized, query_within_limits = _sanitize_grafana_source(normalized)
            sources.append(
                (
                    attribute,
                    raw_source,
                    normalized,
                    sanitized,
                    query_within_limits,
                )
            )

    source_map = {attribute: sanitized for attribute, _raw, _normalized, sanitized, _bounded in sources}
    source_digests = {attribute: canonical_digest(raw_source) for attribute, raw_source, *_rest in sources}
    targets = [
        (attribute, raw_source, normalized, sanitized, query_within_limits, _grafana_url_target(normalized))
        for attribute, raw_source, normalized, sanitized, query_within_limits in sources
    ]
    grafana_targets = [item for item in targets if item[5] is not None]
    if not grafana_targets:
        return None

    source_roles: dict[str, str] = {}
    for attribute, _raw_source, source, _sanitized, _bounded, target in targets:
        rendered = target is not None and "/render/" in urlsplit(source).path.lower()
        if attribute in GRAFANA_LIVE_SOURCE_ATTRIBUTES:
            role = "live"
        elif attribute == "data-image-src":
            role = "fallback"
        elif attribute in {"data-src", "src"}:
            role = "fallback" if tag == "img" or target is None or rendered else "live"
        else:  # pragma: no cover - GRAFANA_SOURCE_ATTRIBUTES is closed
            role = "auxiliary"
        source_roles[attribute] = role

    source_status: dict[str, str] = {}
    for attribute, raw_source, normalized, _sanitized, query_within_limits, _target in targets:
        if source_roles[attribute] == "live":
            source_status[attribute] = _grafana_live_source_status(
                raw_source,
                normalized,
                query_within_limits=query_within_limits,
            )
        else:
            source_status[attribute] = _grafana_fallback_source_status(raw_source, normalized)

    live_sources = [
        (attribute, raw_source, source, sanitized, target)
        for attribute, raw_source, source, sanitized, _bounded, target in targets
        if source_roles[attribute] == "live" and source_status[attribute] == "active-live" and target is not None
    ]
    fallback_sources = [
        (attribute, raw_source, source, sanitized, target)
        for attribute, raw_source, source, sanitized, _bounded, target in targets
        if source_roles[attribute] == "fallback" and source_status[attribute] == "release-image"
    ]
    live_url = next(
        (
            sanitized
            for preferred in (*GRAFANA_LIVE_SOURCE_ATTRIBUTES, "data-src", "src")
            for attribute, _raw_source, _source, sanitized, _target in live_sources
            if attribute == preferred
        ),
        "",
    )
    fallback_url = next(
        (
            sanitized
            for preferred in (*GRAFANA_FALLBACK_SOURCE_ATTRIBUTES, "src")
            for attribute, _raw_source, _source, sanitized, _target in fallback_sources
            if attribute == preferred
        ),
        "",
    )

    target_source = live_url or fallback_url
    target = _grafana_url_target(target_source)
    if target is None:
        target = _grafana_url_target(grafana_targets[0][3]) or grafana_targets[0][5]
    if target is None:
        return None
    conflicts = _grafana_evidence_conflicts(source_map, source_roles, source_status)
    missing_roles: list[str] = []
    if not live_url:
        missing_roles.append("live")
    if not fallback_url:
        missing_roles.append("fallback")
    return {
        "uid": target["uid"],
        "panel_id": target["panel_id"],
        "view_panel": target["view_panel"],
        "query": target["query"],
        "variables": target["variables"],
        "time_range": target["time_range"],
        "live_url": live_url,
        "fallback_url": fallback_url,
        "sources": dict(sorted(source_map.items())),
        "source_digests": dict(sorted(source_digests.items())),
        "source_roles": dict(sorted(source_roles.items())),
        "source_status": dict(sorted(source_status.items())),
        "tag": tag,
        "location": location,
        "visibility_states": list(visibility_states),
        "_conflicts": conflicts,
        "_missing_roles": missing_roles,
    }


class BaseHrefParser(HTMLParser):
    """Find the document's first base href before resolving any URL."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.href = ""
        self.target = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "base":
            return
        for name, value in attrs:
            if name.lower() == "href" and value and not self.href:
                self.href = value
            elif name.lower() == "target" and value and not self.target:
                self.target = value


class StaticPageParser(HTMLParser):
    """Small tolerant HTML inventory parser; no framework-specific selectors."""

    def __init__(
        self,
        *,
        physical_route_value: str,
        origin: str,
        base_href_raw: str = "",
        base_target_raw: str = "",
        limits: dict[str, int] | None = None,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.physical_route = physical_route_value
        self.origin = origin
        self.document_url = _document_base(origin, physical_route_value)
        self.base_url = (
            urljoin(self.document_url, html.unescape(base_href_raw.strip())) if base_href_raw else self.document_url
        )
        self.base_href = (
            normalize_reference(
                base_href_raw,
                route=physical_route_value,
                origin=origin,
                base_url=self.document_url,
            )
            if base_href_raw
            else ""
        )
        self.base_target = base_target_raw.strip() or "_self"
        self.limits = limits or DEFAULT_LIMITS
        self.element_count = 0
        self.articles: list[ContentCollector] = []
        self.mains: list[ContentCollector] = []
        self.bodies: list[ContentCollector] = []
        self.active_articles: list[ContentCollector] = []
        self.active_mains: list[ContentCollector] = []
        self.active_bodies: list[ContentCollector] = []
        self.suppressed_tags: list[str] = []
        self.element_frames: list[ElementFrame] = []
        self.revealable_captures: list[RevealableCapture] = []
        self.active_revealable_captures: list[RevealableCapture] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.lang = ""
        self.meta: dict[str, str] = {}
        self.meta_values: dict[str, list[str]] = {}
        self.canonical_raw = ""
        self.refresh_raw = ""
        self.refresh_records: list[dict[str, str]] = []
        self.redirects: list[dict[str, str]] = []
        self.form_controls: list[dict[str, Any]] = []
        self._form_records: list[dict[str, Any]] = []
        self._form_models: dict[int, dict[str, Any]] = {}
        self._form_ids: dict[str, int] = {}
        self._form_stack: list[int] = []
        self._select_associations: list[tuple[int | None, str | None]] = []
        self.inline_script_parts: list[str] | None = None
        self.features: dict[str, set[str]] = {name: set() for name in FEATURE_NAMES}
        self.suppressed_features: dict[str, set[str]] = {name: set() for name in FEATURE_NAMES}
        self.native_visibility: list[dict[str, Any]] = []
        self.grafana_occurrences: list[dict[str, Any]] = []
        self.grafana_findings: list[dict[str, Any]] = []
        self.html_findings: list[dict[str, Any]] = []
        self.runtime_asset_refs: set[tuple[str, str]] = set()
        self.runtime_references: list[dict[str, str]] = []

    @property
    def collectors(self) -> list[ContentCollector]:
        return [*self.active_bodies, *self.active_mains, *self.active_articles]

    def _location_for(self, tag: str) -> str:
        lineage = {*(frame.tag for frame in self.element_frames), tag}
        if "nav" in lineage:
            return "nav"
        if "header" in lineage:
            return "header"
        if "footer" in lineage:
            return "footer"
        if lineage.intersection({"article", "main"}):
            return "content"
        if "body" in lineage:
            return "body"
        return "head"

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.element_count += 1
        if self.element_count > self.limits["html_elements"]:
            raise OverflowError(f"element limit {self.limits['html_elements']} exceeded")
        if len(self.element_frames) + 1 > self.limits["html_depth"]:
            raise OverflowError(f"depth limit {self.limits['html_depth']} exceeded")
        attrs: dict[str, str] = {}
        for raw_key, raw_value in attrs_list:
            key = raw_key.lower()
            if key in attrs:
                line, column = self.getpos()
                self.html_findings.append(
                    {
                        "code": "duplicate-html-attribute",
                        "attribute": key,
                        "column": column,
                        "line": line,
                        "tag": tag,
                    }
                )
                continue
            # Browsers retain the first occurrence of a duplicated attribute.
            attrs[key] = raw_value or ""
        parent = self.element_frames[-1] if self.element_frames else None
        inherited_visual_states = parent.visual_states if parent else ()
        inherited_accessibility_states = parent.accessibility_states if parent else ()
        disabled_fieldset_depth = parent.disabled_fieldset_depth if parent else 0
        inherited_inert = bool(parent and parent.inert_context)
        if (
            parent
            and parent.tag == "fieldset"
            and parent.disabled_fieldset
            and tag == "legend"
            and not parent.first_legend_seen
        ):
            parent.first_legend_seen = True
            disabled_fieldset_depth = max(0, disabled_fieldset_depth - 1)
        if parent and parent.closed_details:
            if tag == "summary" and not parent.first_summary_seen:
                parent.first_summary_seen = True
            else:
                inherited_visual_states = tuple(sorted({*inherited_visual_states, "closed-details"}))

        own_visual_states = native_visual_states(tag, attrs)
        own_accessibility_states = native_accessibility_states(attrs)
        own_interactivity_states = native_interactivity_states(tag, attrs)
        inherited_interactivity_states = tuple(
            state
            for state, applies in (
                ("disabled-fieldset", disabled_fieldset_depth > 0 and tag in FIELDSET_DISABLED_CONTROL_TAGS),
                ("inert", inherited_inert),
            )
            if applies
        )
        # A closed details element and its first summary remain visible. Its
        # other direct children inherit closed-details above.
        propagated_own_visual_states = tuple(state for state in own_visual_states if state != "closed-details")
        visual_states = tuple(sorted({*inherited_visual_states, *propagated_own_visual_states}))
        accessibility_states = tuple(sorted({*inherited_accessibility_states, *own_accessibility_states}))
        interactivity_states = tuple(sorted({*inherited_interactivity_states, *own_interactivity_states}))
        visible = not visual_states
        interactive = visible and not interactivity_states
        location = self._location_for(tag)
        revealable_root = (
            (tag == "details" and "open" not in attrs)
            or (tag == "dialog" and "open" not in attrs)
            or "popover" in attrs
        )
        if revealable_root:
            capture = RevealableCapture(
                tag=tag,
                depth=len(self.element_frames) + 1,
                location=location,
            )
            self.revealable_captures.append(capture)
            self.active_revealable_captures.append(capture)
        revealable_visible = not (set(visual_states) - REVEALABLE_VISUAL_STATES)
        for capture in self.active_revealable_captures:
            if capture.suppressed_depth:
                if tag not in VOID_TAGS:
                    capture.suppressed_depth += 1
                continue
            if tag in SUPPRESSED_CONTENT_TAGS or not revealable_visible:
                if tag not in VOID_TAGS:
                    capture.suppressed_depth = 1
                continue
            capture.collector.start(
                tag,
                attrs,
                route=self.physical_route,
                origin=self.origin,
                base_url=self.base_url,
                base_target=self.base_target,
            )
        self._inspect_global(
            tag,
            attrs,
            visible=visible,
            interactive=interactive,
            visibility_states=visual_states,
            accessibility_states=accessibility_states,
            interactivity_states=interactivity_states,
            location=location,
        )
        if visual_states or interactivity_states or accessibility_states:
            self.native_visibility.append(
                {
                    "tag": tag,
                    "own_accessibility_states": list(own_accessibility_states),
                    "accessibility_states": list(accessibility_states),
                    "own_visual_states": list(own_visual_states),
                    "visual_states": list(visual_states),
                    "own_interactivity_states": list(own_interactivity_states),
                    "interactivity_states": list(interactivity_states),
                    "location": location,
                }
            )
        if tag not in VOID_TAGS:
            self.element_frames.append(
                ElementFrame(
                    tag=tag,
                    visual_states=visual_states,
                    interactivity_states=interactivity_states,
                    inherited_interactivity_states=inherited_interactivity_states,
                    own_interactivity_states=own_interactivity_states,
                    closed_details=tag == "details" and "open" not in attrs,
                    disabled_fieldset=tag == "fieldset" and "disabled" in attrs,
                    disabled_fieldset_depth=disabled_fieldset_depth + int(tag == "fieldset" and "disabled" in attrs),
                    inert_context=inherited_inert or "inert" in attrs,
                    accessibility_states=accessibility_states,
                )
            )

        if self.suppressed_tags:
            if tag not in VOID_TAGS:
                self.suppressed_tags.append(tag)
            return
        if tag in SUPPRESSED_CONTENT_TAGS or not visible:
            if tag not in VOID_TAGS:
                self.suppressed_tags.append(tag)
            return

        for collector in self.collectors:
            collector.start(
                tag,
                attrs,
                route=self.physical_route,
                origin=self.origin,
                base_url=self.base_url,
                base_target=self.base_target,
            )

        if tag == "body":
            collector = ContentCollector()
            self.bodies.append(collector)
            self.active_bodies.append(collector)
        elif tag == "main":
            collector = ContentCollector()
            self.mains.append(collector)
            self.active_mains.append(collector)
        elif tag == "article":
            collector = ContentCollector()
            self.articles.append(collector)
            self.active_articles.append(collector)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for capture in list(reversed(self.active_revealable_captures)):
            if capture.suppressed_depth:
                capture.suppressed_depth -= 1
            else:
                capture.collector.end(tag)
            if tag == capture.tag and len(self.element_frames) == capture.depth:
                self.active_revealable_captures.remove(capture)
        if tag == "select" and self._select_associations:
            self._select_associations.pop()
        elif tag == "form" and self._form_stack:
            self._form_stack.pop()
        if self.suppressed_tags:
            if tag == "script" and self.inline_script_parts is not None:
                self._finish_inline_script()
            if tag == self.suppressed_tags[-1]:
                self.suppressed_tags.pop()
            self._pop_element_frame(tag)
            return

        for collector in self.collectors:
            collector.end(tag)

        if tag == "article" and self.active_articles:
            self.active_articles.pop()
        elif tag == "main" and self.active_mains:
            self.active_mains.pop()
        elif tag == "body" and self.active_bodies:
            self.active_bodies.pop()
        elif tag == "title":
            self.in_title = False
        self._pop_element_frame(tag)

    def _pop_element_frame(self, tag: str) -> None:
        if any(frame.tag == tag for frame in self.element_frames):
            while self.element_frames:
                if self.element_frames.pop().tag == tag:
                    break

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        # Direct text under a closed details element is part of its collapsed
        # content; only the first summary branch is rendered.
        direct_closed_details = bool(self.element_frames and self.element_frames[-1].closed_details)
        if self.inline_script_parts is not None:
            self.inline_script_parts.append(data)
        for capture in self.active_revealable_captures:
            if not capture.suppressed_depth:
                capture.collector.data(data)
        if self.suppressed_tags or direct_closed_details:
            return
        for collector in self.collectors:
            collector.data(data)

    def _inspect_global(
        self,
        tag: str,
        attrs: dict[str, str],
        *,
        visible: bool,
        interactive: bool,
        visibility_states: tuple[str, ...],
        accessibility_states: tuple[str, ...],
        interactivity_states: tuple[str, ...],
        location: str,
    ) -> None:
        if tag == "base" and attrs.get("href") and not self.base_href:
            raw_base = html.unescape(attrs["href"].strip())
            self.base_url = urljoin(self.document_url, raw_base)
            self.base_href = normalize_reference(
                raw_base,
                route=self.physical_route,
                origin=self.origin,
                base_url=self.document_url,
            )
        self._inspect_global_assets(tag, attrs)
        self._capture_form_semantics(tag, attrs, location)
        detected_features: set[str] = set()
        occurrence = grafana_occurrence(
            tag,
            attrs,
            route=self.physical_route,
            origin=self.origin,
            location=location,
            visibility_states=visibility_states,
            base_url=self.base_url,
        )
        if occurrence is None and any(attrs.get(name) for name in GRAFANA_FALLBACK_SOURCE_ATTRIBUTES):
            identity = " ".join(attrs.get(key, "") for key in ("class", "id", "role")).lower()
            if "grafana" in identity:
                sources = {
                    name: html.unescape(attrs[name].strip())
                    for name in GRAFANA_FALLBACK_SOURCE_ATTRIBUTES
                    if attrs.get(name)
                }
                self.grafana_findings.append(
                    {
                        "code": "grafana-live-missing",
                        "location": location,
                        "source_digests": {key: canonical_digest(value) for key, value in sources.items()},
                    }
                )
        if occurrence is not None:
            conflicts = occurrence.pop("_conflicts")
            missing_roles = occurrence.pop("_missing_roles")
            self.grafana_occurrences.append(occurrence)
            if conflicts:
                self.grafana_findings.append(
                    {
                        "code": "grafana-source-conflict",
                        "location": location,
                        "conflicts": conflicts,
                        "source_digests": dict(occurrence["source_digests"]),
                    }
                )
            for role in missing_roles:
                self.grafana_findings.append(
                    {
                        "code": f"grafana-{role}-missing",
                        "location": location,
                        "source_digests": dict(occurrence["source_digests"]),
                    }
                )
        if tag == "html":
            self.lang = normalize_text(attrs.get("lang", "")).lower()
        elif tag == "title" and location == "head" and not self.suppressed_tags:
            # Only the document's head title contributes page metadata. SVG
            # and template subtrees may carry their own accessibility titles;
            # those tags are inspected before the suppression stack is
            # updated, so accepting every <title> here leaks following page
            # text into the document title when their suppressed end tag
            # returns early.
            self.in_title = True
        elif tag == "meta":
            key = (attrs.get("name") or attrs.get("property") or "").lower()
            if key:
                value = normalize_text(attrs.get("content", ""))
                self.meta.setdefault(key, value)
                self.meta_values.setdefault(key, []).append(value)
            if key in {"og:image", "twitter:image"} and attrs.get("content"):
                source = normalize_reference(
                    attrs["content"],
                    route=self.physical_route,
                    origin=self.origin,
                    base_url=self.base_url,
                )
                if is_local_reference(source):
                    self.runtime_asset_refs.add((urlsplit(source).path, "metadata"))
            if attrs.get("http-equiv", "").lower() == "refresh":
                refresh_raw = attrs.get("content", "")
                if not self.refresh_raw:
                    self.refresh_raw = refresh_raw
                parsed_refresh = self._parse_refresh(refresh_raw)
                if parsed_refresh:
                    self.refresh_records.append(parsed_refresh)
        elif tag == "link":
            rel = {value.lower() for value in attrs.get("rel", "").split()}
            href = attrs.get("href", "")
            if "canonical" in rel and href and not self.canonical_raw:
                self.canonical_raw = href
            if "alternate" in rel and (
                "rss" in attrs.get("type", "").lower() or "atom" in attrs.get("type", "").lower()
            ):
                detected_features.add("rss")
            if href and rel.intersection({"alternate", "icon", "manifest", "modulepreload", "preload", "stylesheet"}):
                normalized = normalize_reference(
                    href,
                    route=self.physical_route,
                    origin=self.origin,
                    base_url=self.base_url,
                )
                if is_local_reference(normalized):
                    asset_path = urlsplit(normalized).path
                    self.runtime_asset_refs.add((asset_path, "link"))
                    if "manifest" in rel:
                        self.runtime_asset_refs.add((asset_path, "metadata"))
                    suffix = Path(urlsplit(normalized).path).suffix.lower()
                    runtime_roles: list[tuple[str, str]] = []
                    if "stylesheet" in rel:
                        runtime_roles.append(("stylesheet", "stylesheet"))
                    if "modulepreload" in rel:
                        runtime_roles.append(("modulepreload", "modulepreload"))
                    if "preload" in rel:
                        preload_as = attrs.get("as", "").lower()
                        preload_kind = PRELOAD_AS_KINDS.get(preload_as)
                        if preload_kind is None and suffix == ".wasm":
                            preload_kind = "preload-wasm"
                        if preload_kind:
                            runtime_roles.append(("preload", preload_kind))
                    for rel_role, kind in runtime_roles:
                        self.runtime_asset_refs.add((asset_path, kind))
                        self.runtime_references.append(
                            _runtime_reference(
                                attrs=attrs,
                                href=normalized,
                                kind=kind,
                                rel=rel_role,
                                route=self.physical_route,
                                origin=self.origin,
                                base_url=self.base_url,
                            )
                        )
                    if "preload" in rel and attrs.get("imagesrcset"):
                        for raw_source, _descriptor in _parse_srcset(attrs["imagesrcset"]):
                            source = normalize_reference(
                                raw_source,
                                route=self.physical_route,
                                origin=self.origin,
                                base_url=self.base_url,
                            )
                            if is_local_reference(source):
                                self.runtime_asset_refs.add((urlsplit(source).path, "preload-image"))
        elif tag == "script" and attrs.get("src"):
            source = normalize_reference(
                attrs["src"],
                route=self.physical_route,
                origin=self.origin,
                base_url=self.base_url,
            )
            if is_local_reference(source):
                kind = "module" if attrs.get("type", "").lower() == "module" else "script"
                self.runtime_asset_refs.add((urlsplit(source).path, kind))
                self.runtime_references.append(
                    _runtime_reference(
                        attrs=attrs,
                        href=source,
                        kind=kind,
                        rel="script",
                        route=self.physical_route,
                        origin=self.origin,
                        base_url=self.base_url,
                    )
                )
        elif tag == "script" and not attrs.get("src"):
            self.inline_script_parts = []

        values = " ".join(f"{key}={value}" for key, value in sorted(attrs.items())).lower()
        identity = " ".join(attrs.get(key, "") for key in ("class", "id", "role", "aria-label", "title")).lower()
        feature_identity = f"{tag} {identity} {values}"
        if (
            (tag == "input" and attrs.get("type", "").lower() == "search")
            or attrs.get("role", "").lower()
            in {
                "search",
                "searchbox",
            }
            or (
                tag in {"button", "form", "input", "search", "site-search"}
                and re.search(r"(?:^|[\s/_-])(pagefind|search)(?:$|[\s/_-])", feature_identity)
            )
        ):
            detected_features.add("search")
        interactive_control = tag in {"button", "input", "select"} or "-" in tag
        if interactive_control and (
            re.search(r"dark.?mode|theme.?(?:select|toggle)|color.?scheme", feature_identity)
            or re.search(r"\b(appearance|dark|theme)\b", feature_identity)
        ):
            detected_features.add("dark")
        if interactive_control and re.search(r"reader.?mode|reading.?mode|readermode", feature_identity):
            detected_features.add("reader")
        if "breadcrumb" in feature_identity or "breadcrumblist" in values:
            detected_features.add("breadcrumbs")
        if tag == "math" or re.search(r"(?:^|[\s/_-])(katex|mathjax)(?:$|[\s/_-])", feature_identity):
            detected_features.add("katex")
        evidence = (
            f"{tag}:{values};location={location};"
            f"visual={','.join(visibility_states) or 'native-visible'};"
            f"accessibility={','.join(accessibility_states) or 'accessibility-exposed'};"
            f"interactivity={','.join(interactivity_states) or 'native-interactive'}"
        )
        for feature in detected_features:
            requires_interaction = feature in {"breadcrumbs", "dark", "reader", "search"}
            target = (
                self.features if visible and (interactive or not requires_interaction) else self.suppressed_features
            )
            target[feature].add(evidence)

    def _inspect_global_assets(self, tag: str, attrs: dict[str, str]) -> None:
        """Inventory local HTML resources even when they are outside page content."""
        candidates: list[str] = []
        if tag in {"audio", "embed", "iframe", "img", "input", "script", "source", "track", "video"}:
            if attrs.get("src"):
                candidates.append(attrs["src"])
        if tag == "object" and attrs.get("data"):
            candidates.append(attrs["data"])
        if attrs.get("data-src"):
            candidates.append(attrs["data-src"])
        for attribute in (*GRAFANA_LIVE_SOURCE_ATTRIBUTES, "data-image-src"):
            if attrs.get(attribute):
                candidates.append(attrs[attribute])
        if tag == "video" and attrs.get("poster"):
            candidates.append(attrs["poster"])
        if tag in {"img", "source"} and attrs.get("srcset"):
            candidates.extend(source for source, _descriptor in _parse_srcset(attrs["srcset"]))
        if tag == "a" and attrs.get("href"):
            href = attrs["href"]
            if "download" in attrs or Path(urlsplit(href).path).suffix.lower() in DOWNLOAD_SUFFIXES:
                candidates.append(href)

        for raw_source in candidates:
            source = normalize_reference(
                raw_source,
                route=self.physical_route,
                origin=self.origin,
                base_url=self.base_url,
            )
            if is_local_reference(source):
                self.runtime_asset_refs.add((urlsplit(source).path, "html"))

    def _parse_refresh(self, raw_value: str) -> dict[str, str] | None:
        match = META_REFRESH_RE.match(raw_value)
        if not match:
            return None
        raw_target = next(
            (match.group(name) for name in ("double", "single", "bare") if match.group(name) is not None),
            "",
        ).strip()
        return {
            "delay": match.group("delay"),
            "target": normalize_reference(
                raw_target,
                route=self.physical_route,
                origin=self.origin,
                route_like=True,
                base_url=self.base_url,
            ),
        }

    def _capture_form_semantics(self, tag: str, attrs: dict[str, str], location: str) -> None:
        if tag not in {"button", "form", "input", "option", "select", "textarea"}:
            return
        value_digest = ""
        if "value" in attrs:
            value_digest = f"sha256:{hashlib.sha256(attrs['value'].encode()).hexdigest()}"
        control_type = _effective_control_type(tag, attrs.get("type", ""))
        common = {
            "tag": tag,
            "type": control_type,
            "name": attrs.get("name", "").strip(),
            "value_digest": value_digest,
            "checked": "checked" in attrs,
            "required": "required" in attrs,
            "readonly": "readonly" in attrs,
            "selected": "selected" in attrs,
            "multiple": "multiple" in attrs,
            "disabled": "disabled" in attrs,
            "location": location,
        }

        if tag == "form":
            token = len(self._form_models)
            form_id = attrs.get("id", "").strip()
            raw_action = attrs.get("action", "")
            action = normalize_reference(
                raw_action if raw_action.strip() else self.document_url,
                route=self.physical_route,
                origin=self.origin,
                route_like=True,
                base_url=self.base_url,
            )
            model = {
                "action": action,
                "browsing_target": attrs.get("target", "").strip() or self.base_target,
                "enctype": _effective_form_enctype(attrs.get("enctype", "")),
                "form_id": form_id,
                "method": _effective_form_method(attrs.get("method", "")),
                "no_validate": "novalidate" in attrs,
            }
            self._form_models[token] = model
            if form_id:
                self._form_ids.setdefault(form_id, token)
            self._form_stack.append(token)
            self._form_records.append({**common, "_form_token": token, "_is_form": True})
            return

        explicit_form_id = attrs.get("form", "").strip() if "form" in attrs else None
        owner_token = None if explicit_form_id is not None else (self._form_stack[-1] if self._form_stack else None)
        if tag == "option" and explicit_form_id is None and self._select_associations:
            owner_token, explicit_form_id = self._select_associations[-1]
        if tag == "select":
            self._select_associations.append((owner_token, explicit_form_id))
        self._form_records.append(
            {
                **common,
                "_explicit_form_id": explicit_form_id,
                "_form_token": owner_token,
                "_formenctype": attrs.get("formenctype", ""),
                "_formenctype_present": "formenctype" in attrs,
                "_formaction": attrs.get("formaction", ""),
                "_formaction_present": "formaction" in attrs,
                "_formmethod": attrs.get("formmethod", ""),
                "_formmethod_present": "formmethod" in attrs,
                "_formnovalidate": "formnovalidate" in attrs,
                "_formtarget": attrs.get("formtarget", ""),
                "_formtarget_present": "formtarget" in attrs,
                "_is_form": False,
            }
        )

    def resolve_form_controls(self) -> None:
        """Resolve descendant and forward/external ``form=`` ownership once."""
        resolved: list[dict[str, Any]] = []
        for raw in self._form_records:
            is_form = raw["_is_form"]
            explicit_id = raw.get("_explicit_form_id")
            token = raw.get("_form_token")
            if not is_form and explicit_id is not None:
                token = self._form_ids.get(explicit_id)
            model = self._form_models.get(token) if token is not None else None
            public = {key: value for key, value in raw.items() if not key.startswith("_")}
            if model is None:
                public.update(
                    {
                        "browsing_target": "",
                        "enctype": "",
                        "form_associated": False,
                        "form_owner": "",
                        "method": "",
                        "no_validate": False,
                        "target": "",
                    }
                )
                resolved.append(public)
                continue

            owner = model["form_id"] or ("self" if is_form else "ancestor")
            target = model["action"]
            method = model["method"]
            enctype = model["enctype"]
            browsing_target = model["browsing_target"]
            no_validate = model["no_validate"]
            submitter = raw["tag"] == "button" and raw["type"] == "submit"
            submitter = submitter or raw["tag"] == "input" and raw["type"] in {"image", "submit"}
            if submitter and not is_form:
                if raw["_formaction_present"]:
                    target = normalize_reference(
                        raw["_formaction"] if raw["_formaction"].strip() else self.document_url,
                        route=self.physical_route,
                        origin=self.origin,
                        route_like=True,
                        base_url=self.base_url,
                    )
                if raw["_formmethod_present"]:
                    method = _effective_form_method(raw["_formmethod"])
                if raw["_formenctype_present"]:
                    enctype = _effective_form_enctype(raw["_formenctype"])
                if raw["_formtarget_present"]:
                    browsing_target = raw["_formtarget"].strip() or "_self"
                no_validate = no_validate or raw["_formnovalidate"]
            public.update(
                {
                    "browsing_target": browsing_target,
                    "enctype": enctype,
                    "form_associated": True,
                    "form_owner": owner,
                    "method": method,
                    "no_validate": no_validate,
                    "target": target,
                }
            )
            resolved.append(public)
        self.form_controls = resolved

    def _finish_inline_script(self) -> None:
        script = "".join(self.inline_script_parts or [])
        patterns = (
            (
                "assign",
                re.compile(r"(?:(?:window|document)\.)?location\.assign\(\s*(['\"])(.*?)\1\s*\)", re.DOTALL),
            ),
            (
                "replace",
                re.compile(r"(?:(?:window|document)\.)?location\.replace\(\s*(['\"])(.*?)\1\s*\)", re.DOTALL),
            ),
            (
                "location",
                re.compile(r"(?:(?:window|document)\.)?location(?:\.href)?\s*=\s*(['\"])(.*?)\1", re.DOTALL),
            ),
        )
        for kind, pattern in patterns:
            for match in pattern.finditer(script):
                target = normalize_reference(
                    match.group(2),
                    route=self.physical_route,
                    origin=self.origin,
                    route_like=True,
                    base_url=self.base_url,
                )
                self.redirects.append({"kind": kind, "target": target})
        self.inline_script_parts = None

    def refresh(self) -> tuple[str, str]:
        if not self.refresh_records:
            return "", ""
        return self.refresh_records[0]["delay"], self.refresh_records[0]["target"]

    def refresh_target(self) -> str:
        return self.refresh()[1]

    def metadata(self) -> dict[str, Any]:
        robots = sorted(
            {
                token.lower()
                for value in self.meta_values.get("robots", [])
                for token in re.split(r"[\s,]+", value)
                if token
            }
        )
        robots_by_crawler = {
            key: sorted(
                {
                    token.lower()
                    for value in self.meta_values.get(key, [])
                    for token in re.split(r"[\s,]+", value)
                    if token
                }
            )
            for key in sorted(ROBOT_META_NAMES & self.meta_values.keys())
        }
        canonical = ""
        if self.canonical_raw:
            canonical = normalize_reference(
                self.canonical_raw,
                route=self.physical_route,
                origin=self.origin,
                route_like=True,
                base_url=self.base_url,
            )
        return {
            "base_href": self.base_href,
            "title": normalize_text("".join(self.title_parts)),
            "description": self.meta.get("description", ""),
            "canonical": canonical,
            "lang": self.lang,
            "robots": robots,
            "robots_by_crawler": robots_by_crawler,
            "noindex": "noindex" in robots,
            "refreshes": self.refresh_records,
            "open_graph": {
                "description": self.meta.get("og:description", ""),
                "image": normalize_reference(
                    self.meta.get("og:image", ""),
                    route=self.physical_route,
                    origin=self.origin,
                    base_url=self.base_url,
                )
                if self.meta.get("og:image")
                else "",
                "title": self.meta.get("og:title", ""),
                "type": self.meta.get("og:type", ""),
            },
            "twitter": {
                "card": self.meta.get("twitter:card", ""),
                "description": self.meta.get("twitter:description", ""),
                "image": normalize_reference(
                    self.meta.get("twitter:image", ""),
                    route=self.physical_route,
                    origin=self.origin,
                    base_url=self.base_url,
                )
                if self.meta.get("twitter:image")
                else "",
                "title": self.meta.get("twitter:title", ""),
            },
        }

    def selected_collectors(self) -> list[ContentCollector]:
        # Compare authored page content independently from replaceable global
        # presentation. Article-local footers (for example topic links) remain
        # semantic evidence; site header/nav/footer and sibling runtime chrome do not.
        return self.articles or self.mains or self.bodies


def _merge_collectors(collectors: list[ContentCollector]) -> dict[str, Any]:
    return {
        # HTML data callbacks and inline presentation elements do not create a
        # rendered word boundary. ContentCollector.start() already inserts one
        # for block/boundary tags, so adding spaces between every callback here
        # makes entity encoding and syntax-highlighter span choices look like
        # semantic text changes (for example ``<ref>`` versus ``<re f>``).
        "text": normalize_text(" ".join("".join(collector.text_parts) for collector in collectors)),
        "headings": [item for collector in collectors for item in collector.headings],
        "tables": [item for collector in collectors for item in collector.tables],
        "links": [item for collector in collectors for item in collector.links],
        "downloads": [item for collector in collectors for item in collector.downloads],
        "media": [item for collector in collectors for item in collector.media],
        "asset_refs": sorted({item for collector in collectors for item in collector.asset_refs}),
        "fragment_targets": sorted({item for collector in collectors for item in collector.fragment_targets}),
    }


def _route_for_page(parser: StaticPageParser, metadata: dict[str, Any]) -> str:
    if parser.physical_route in {"/404", "/404.html"}:
        return "/404"
    canonical = metadata["canonical"]
    if canonical:
        parts = urlsplit(canonical)
        if not parts.scheme and not parts.netloc:
            return normalize_route(parts.path)
    return parser.physical_route


def _page_manifest(parser: StaticPageParser, *, source: str) -> tuple[str, dict[str, Any]]:
    metadata = parser.metadata()
    route = _route_for_page(parser, metadata)
    merged = _merge_collectors(parser.selected_collectors())
    revealable: list[dict[str, Any]] = []
    revealable_asset_refs: set[tuple[str, str]] = set()
    revealable_fragment_targets: set[str] = set()
    for capture in parser.revealable_captures:
        content = _merge_collectors([capture.collector])
        revealable_asset_refs.update(content.pop("asset_refs"))
        revealable_fragment_targets.update(content.pop("fragment_targets"))
        revealable.append(
            {
                "kind": capture.tag,
                "location": capture.location,
                **content,
            }
        )
    page = {
        "source": source,
        "metadata": metadata,
        "text": merged["text"],
        "headings": merged["headings"],
        "tables": merged["tables"],
        "links": merged["links"],
        "downloads": merged["downloads"],
        "media": merged["media"],
        "fragment_targets": sorted({*merged["fragment_targets"], *revealable_fragment_targets}),
        "grafana": parser.grafana_occurrences,
        "runtime": parser.runtime_references,
        "native_visibility": parser.native_visibility,
        "form_controls": parser.form_controls,
        "redirects": [
            *({"kind": "meta-refresh", **record} for record in parser.refresh_records),
            *parser.redirects,
        ],
        "revealable": revealable,
    }
    return route, {**page, "_asset_refs": sorted({*merged["asset_refs"], *revealable_asset_refs})}


def _asset_record(
    root: Path,
    web_path: str,
    references: set[tuple[str, str]],
    *,
    limits: dict[str, int],
    verifier: StageBoundaryVerifier,
) -> dict[str, Any]:
    decoded = unquote(web_path).lstrip("/")
    candidates = [root / decoded, root / decoded / "index.html"]
    if not decoded.endswith(".html"):
        candidates.append(root / f"{decoded}.html")
    resolved = candidates[0]
    exists = False
    for candidate in candidates:
        try:
            candidate.relative_to(root)
            candidate_stat = candidate.lstat()
        except (OSError, ValueError):
            continue
        if stat.S_ISREG(candidate_stat.st_mode):
            resolved = candidate
            exists = True
            break
    decoded_media_type = ""
    suffix = Path(urlsplit(web_path).path).suffix.lower()
    expected_media_type = IMAGE_MIME_BY_SUFFIX.get(suffix)
    size: int | None = None
    digest: str | None = None
    webvtt_header = False
    if exists and expected_media_type:
        value = verifier.read_bytes(resolved, min(limits["asset_bytes"], limits["image_bytes"]))
        size = len(value)
        digest = hashlib.sha256(value).hexdigest()
        decoded_media_type = (
            _decoded_image_mime(
                value,
                expected_media_type,
                maximum_pixels=limits["image_pixels"],
                maximum_decoded_bytes=limits["image_decoded_bytes"],
                maximum_frames=limits["image_frames"],
            )
            or ""
        )
    elif exists and suffix in HLS_MIME_BY_SUFFIX:
        if suffix == ".vtt":
            value = verifier.read_bytes(resolved, min(limits["asset_bytes"], limits["hls_bytes"]))
            size = len(value)
            valid_webvtt, webvtt_header = _webvtt_payload_status(value, size)
            decoded_media_type = "text/vtt" if valid_webvtt else ""
            digest = hashlib.sha256(value).hexdigest()
        else:
            size, digest = verifier.sha256_file(resolved, limits["asset_bytes"])
            prefix, prefix_size = _safe_read_prefix(root, resolved, limits["asset_bytes"])
            if prefix_size != size:
                raise SafeFileError("unsafe-tree-identity-change", "release media changed between evidence reads")
            decoded_media_type = _hls_detected_media_type(prefix, size, suffix)
    elif exists:
        size, digest = verifier.sha256_file(resolved, limits["asset_bytes"])
    return {
        "decoded_media_type": decoded_media_type,
        "exists": exists,
        "size": size,
        "sha256": digest,
        "webvtt_header": webvtt_header,
        "references": [
            {"kind": kind, "route": route} for route, kind in sorted(references, key=lambda item: (item[0], item[1]))
        ],
    }


def _png_scanline_layout(
    width: int, height: int, bit_depth: int, color_type: int, interlace: int
) -> list[tuple[int, int, int]] | None:
    allowed_depths = {
        0: {1, 2, 4, 8, 16},
        2: {8, 16},
        3: {1, 2, 4, 8},
        4: {8, 16},
        6: {8, 16},
    }
    if bit_depth not in allowed_depths.get(color_type, set()):
        return None
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bits_per_pixel = channels * bit_depth
    passes = (
        [(0, 0, 1, 1)]
        if interlace == 0
        else [
            (0, 0, 8, 8),
            (4, 0, 8, 8),
            (0, 4, 4, 8),
            (2, 0, 4, 4),
            (0, 2, 2, 4),
            (1, 0, 2, 2),
            (0, 1, 1, 2),
        ]
    )
    layout: list[tuple[int, int, int]] = []
    for x_start, y_start, x_step, y_step in passes:
        pass_width = 0 if width <= x_start else (width - x_start + x_step - 1) // x_step
        pass_height = 0 if height <= y_start else (height - y_start + y_step - 1) // y_step
        if pass_width and pass_height:
            layout.append((pass_height, (pass_width * bits_per_pixel + 7) // 8, max(1, (bits_per_pixel + 7) // 8)))
    return layout


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    return above if above_distance <= upper_left_distance else upper_left


def _decode_png_scanlines(decoded: bytes, layout: list[tuple[int, int, int]]) -> bool:
    cursor = 0
    for rows, row_bytes, bytes_per_pixel in layout:
        previous = bytearray(row_bytes)
        for _row in range(rows):
            filter_type = decoded[cursor]
            if filter_type > 4:
                return False
            cursor += 1
            encoded = decoded[cursor : cursor + row_bytes]
            if len(encoded) != row_bytes:
                return False
            reconstructed = bytearray(row_bytes)
            for index, byte in enumerate(encoded):
                left = reconstructed[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                above = previous[index]
                upper_left = previous[index - bytes_per_pixel] if index >= bytes_per_pixel else 0
                predictor = (
                    0
                    if filter_type == 0
                    else left
                    if filter_type == 1
                    else above
                    if filter_type == 2
                    else (left + above) // 2
                    if filter_type == 3
                    else _paeth_predictor(left, above, upper_left)
                )
                reconstructed[index] = (byte + predictor) & 0xFF
            previous = reconstructed
            cursor += row_bytes
    return cursor == len(decoded)


def _valid_png(value: bytes, maximum_pixels: int, maximum_decoded_bytes: int) -> bool:
    if not value.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    layout: list[tuple[int, int, int]] | None = None
    compressed_parts: list[bytes] = []
    saw_idat = False
    ended_idat = False
    saw_iend = False
    saw_palette = False
    color_type = -1
    bit_depth = -1
    while offset + 12 <= len(value):
        length = struct.unpack(">I", value[offset : offset + 4])[0]
        chunk_type = value[offset + 4 : offset + 8]
        if not re.fullmatch(rb"[A-Za-z]{4}", chunk_type) or not chr(chunk_type[2]).isupper():
            return False
        data_start = offset + 8
        data_end = data_start + length
        crc_end = data_end + 4
        if crc_end > len(value):
            return False
        chunk = value[data_start:data_end]
        expected_crc = struct.unpack(">I", value[data_end:crc_end])[0]
        if binascii.crc32(chunk, binascii.crc32(chunk_type)) & 0xFFFFFFFF != expected_crc:
            return False
        if chunk_type == b"IHDR":
            if layout is not None or length != 13 or offset != 8:
                return False
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunk)
            if (
                not width
                or not height
                or width * height > maximum_pixels
                or compression != 0
                or filtering != 0
                or interlace not in {0, 1}
            ):
                return False
            layout = _png_scanline_layout(width, height, bit_depth, color_type, interlace)
            if layout is None:
                return False
        elif chunk_type == b"IDAT":
            if layout is None or saw_iend or ended_idat:
                return False
            saw_idat = True
            compressed_parts.append(chunk)
        elif chunk_type == b"PLTE":
            if (
                saw_palette
                or saw_idat
                or color_type in {0, 4}
                or not 3 <= length <= 768
                or length % 3
                or (color_type == 3 and length // 3 > 2**bit_depth)
            ):
                return False
            saw_palette = True
        elif chunk_type == b"IEND":
            if length or not saw_idat:
                return False
            saw_iend = True
            offset = crc_end
            break
        elif saw_idat:
            ended_idat = True
        elif chunk_type[:1].isupper() and chunk_type not in {b"IHDR", b"PLTE"}:
            return False
        offset = crc_end
    if layout is None or not saw_idat or not saw_iend or offset != len(value) or (color_type == 3 and not saw_palette):
        return False
    expected_size = sum(rows * (1 + row_bytes) for rows, row_bytes, _bytes_per_pixel in layout)
    if expected_size > maximum_decoded_bytes:
        return False
    try:
        decompressor = zlib.decompressobj()
        decoded_parts: list[bytes] = []
        decoded_size = 0
        for compressed in compressed_parts:
            chunk = decompressor.decompress(compressed, expected_size + 1 - decoded_size)
            decoded_parts.append(chunk)
            decoded_size += len(chunk)
            if decoded_size > expected_size or decompressor.unconsumed_tail:
                return False
        decoded = b"".join(decoded_parts)
    except (MemoryError, zlib.error):
        return False
    if (
        not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
        or len(decoded) != expected_size
    ):
        return False
    return _decode_png_scanlines(decoded, layout)


def _pillow_decodes_image(
    value: bytes,
    expected_mime: str,
    *,
    maximum_pixels: int,
    maximum_decoded_bytes: int,
    maximum_frames: int,
) -> bool:
    expected_format = {
        "image/avif": "AVIF",
        "image/gif": "GIF",
        "image/jpeg": "JPEG",
        "image/webp": "WEBP",
    }.get(expected_mime)
    if expected_format is None:
        return False
    try:
        # Pillow reports decompression bombs as either a warning or an error,
        # depending on size.  Both are invalid release images, and neither may
        # escape as an unbounded traceback from the parity gate.
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            from PIL import Image

            with Image.open(io.BytesIO(value)) as image:
                if image.format != expected_format or image.width * image.height > maximum_pixels:
                    return False
                frame_count = getattr(image, "n_frames", 1)
                if frame_count > maximum_frames:
                    return False
                decoded_bytes = 0
                for frame in range(frame_count):
                    image.seek(frame)
                    decoded_bytes += image.width * image.height * max(1, len(image.getbands()))
                    if decoded_bytes > maximum_decoded_bytes:
                        return False
                    image.load()
            with Image.open(io.BytesIO(value)) as verified:
                verified.verify()
    except Exception:  # noqa: BLE001 - every decoder/plugin failure is a closed invalid-image result
        return False
    return True


def _decoded_image_mime(
    value: bytes,
    expected_mime: str,
    *,
    maximum_pixels: int,
    maximum_decoded_bytes: int,
    maximum_frames: int,
) -> str | None:
    """Decode a bounded image payload; shallow signatures never suffice."""
    if expected_mime == "image/png":
        valid = _valid_png(value, maximum_pixels, maximum_decoded_bytes)
    else:
        valid = _pillow_decodes_image(
            value,
            expected_mime,
            maximum_pixels=maximum_pixels,
            maximum_decoded_bytes=maximum_decoded_bytes,
            maximum_frames=maximum_frames,
        )
    return expected_mime if valid else None


def _grafana_fallback_file_findings(
    routes: dict[str, dict[str, Any]],
    assets: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for route, page in routes.items():
        for occurrence in page["grafana"]:
            for source_attribute, role in occurrence["source_roles"].items():
                if role != "fallback" or occurrence["source_status"][source_attribute] != "release-image":
                    continue
                fallback = occurrence["sources"][source_attribute]
                parts = urlsplit(fallback)
                expected_mime = IMAGE_MIME_BY_SUFFIX[Path(parts.path).suffix.lower()]
                asset = assets.get(parts.path)
                reason = "missing" if not asset or not asset["exists"] else "invalid-image"
                if asset and asset["decoded_media_type"] == expected_mime:
                    continue
                findings.append(
                    {
                        "code": "grafana-fallback-invalid",
                        "location": occurrence["location"],
                        "path": parts.path,
                        "reason": reason,
                        "reference_digest": occurrence["source_digests"][source_attribute],
                        "route": route,
                        "source_attribute": source_attribute,
                    }
                )
    return findings


def _runtime_integrity_findings(routes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for route, page in routes.items():
        for index, reference in enumerate(page["runtime"]):
            if reference["kind"] != "preload-image":
                continue
            reasons: set[str] = set()
            href_parts = urlsplit(reference["href"])
            expected_mime = IMAGE_MIME_BY_SUFFIX.get(Path(href_parts.path).suffix.lower())
            if expected_mime is None:
                reasons.add("unsupported-image-extension")
            elif reference["type"] and reference["type"] != expected_mime:
                reasons.add("image-type-mismatch")
            for srcset_source, _descriptor in _parse_srcset(reference["imagesrcset"]):
                if not srcset_source:
                    continue
                parts = urlsplit(srcset_source)
                if parts.scheme or parts.netloc or not parts.path:
                    reasons.add("nonlocal-imagesrcset")
                elif Path(parts.path).suffix.lower() not in IMAGE_MIME_BY_SUFFIX:
                    reasons.add("unsupported-imagesrcset-extension")
            if reasons:
                findings.append(
                    {
                        "code": "invalid-runtime-reference",
                        "index": index,
                        "reasons": sorted(reasons),
                        "route": route,
                    }
                )
    return sorted(findings, key=canonical_json)


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _xml_namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") and "}" in tag else ""


ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
XML_BASE_ATTRIBUTE = f"{{{XML_NAMESPACE}}}base"
SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
RSS_CONTENT_NAMESPACE = "http://purl.org/rss/1.0/modules/content/"
RSS_DC_NAMESPACE = "http://purl.org/dc/elements/1.1/"


def _xml_children(element: ET.Element, name: str, namespace: str) -> list[ET.Element]:
    return [child for child in element if _xml_local_name(child.tag) == name and _xml_namespace(child.tag) == namespace]


def _direct_child_text(element: ET.Element, name: str, namespace: str = "") -> str:
    for child in element:
        if _xml_local_name(child.tag) == name and _xml_namespace(child.tag) == namespace:
            return normalize_text("".join(child.itertext()))
    return ""


class _XmlCanonicalBudget:
    """Stream bounded namespace-qualified XML semantics into stable digests."""

    def __init__(self, *, maximum_nodes: int, maximum_bytes: int) -> None:
        self.maximum_nodes = maximum_nodes
        self.maximum_bytes = maximum_bytes
        self.nodes = 0
        self.bytes = 0

    def digest(
        self,
        element: ET.Element,
        *,
        excluded_direct_ids: frozenset[int] = frozenset(),
        context: tuple[tuple[str, str], ...] = (),
    ) -> str:
        digest = hashlib.sha256()

        def emit(token: list[str]) -> None:
            encoded = (canonical_json(token) + "\n").encode("utf-8")
            self.nodes += 1
            self.bytes += len(encoded)
            if self.nodes > self.maximum_nodes:
                raise OverflowError("canonical XML node limit exceeded")
            if self.bytes > self.maximum_bytes:
                raise OverflowError("canonical XML byte limit exceeded")
            digest.update(encoded)

        for name, value in sorted(context):
            emit(["context", name, normalize_text(value)])
        pending: list[tuple[str, Any, bool]] = [("element", element, True)]
        while pending:
            event, value, is_root = pending.pop()
            if event == "tail":
                normalized_tail = normalize_text(value)
                if normalized_tail:
                    emit(["tail", normalized_tail])
                continue
            if event == "end":
                emit(["end", value])
                continue
            current: ET.Element = value
            emit(["start", current.tag])
            for attribute_name, attribute_value in sorted(current.attrib.items()):
                emit(["attribute", attribute_name, normalize_text(attribute_value)])
            normalized_text = normalize_text(current.text or "")
            if normalized_text:
                emit(["text", normalized_text])
            pending.append(("end", current.tag, False))
            for child in reversed(list(current)):
                pending.append(("tail", child.tail or "", False))
                if not (is_root and id(child) in excluded_direct_ids):
                    pending.append(("element", child, False))
        return f"sha256:{digest.hexdigest()}"


def _atom_entry_link(element: ET.Element) -> str:
    fallback = ""
    for child in element:
        if (
            _xml_local_name(child.tag) != "link"
            or _xml_namespace(child.tag) != ATOM_NAMESPACE
            or not child.attrib.get("href")
        ):
            continue
        if child.attrib.get("rel", "alternate").lower() == "alternate":
            return child.attrib["href"]
        fallback = fallback or child.attrib["href"]
    return fallback


def _bounded_bytes(path: Path, maximum: int) -> bytes:
    """Descriptor-walk and read one stable single-link regular file."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    relative_parts = absolute.parts[1:]
    if not relative_parts:
        raise OSError("manifest input must name a file")
    directory_descriptors: list[int] = []
    directory_records: list[tuple[int, str, int, os.stat_result]] = []
    file_descriptor = -1
    try:
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise OSError("O_NOFOLLOW is required for manifest reads")
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | nofollow
        root_before = os.stat(os.sep, follow_symlinks=False)
        root_descriptor = os.open(os.sep, directory_flags)
        directory_descriptors.append(root_descriptor)
        root_opened = os.fstat(root_descriptor)
        if not stat.S_ISDIR(root_opened.st_mode) or _file_identity(root_opened) != _file_identity(root_before):
            raise OSError("manifest root changed while opening")
        current_descriptor = root_descriptor
        for component in relative_parts[:-1]:
            before_directory = os.stat(component, dir_fd=current_descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(before_directory.st_mode):
                raise OSError("manifest path component is not a regular directory")
            child_descriptor = os.open(
                component,
                directory_flags,
                dir_fd=current_descriptor,
            )
            try:
                child_opened = os.fstat(child_descriptor)
                if not stat.S_ISDIR(child_opened.st_mode) or _file_identity(child_opened) != _file_identity(
                    before_directory
                ):
                    raise OSError("manifest directory changed while opening")
            except BaseException:
                os.close(child_descriptor)
                raise
            directory_records.append((current_descriptor, component, child_descriptor, child_opened))
            directory_descriptors.append(child_descriptor)
            current_descriptor = child_descriptor

        leaf_name = relative_parts[-1]
        before = os.stat(leaf_name, dir_fd=current_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("manifest input is not a regular file")
        if before.st_nlink != 1:
            raise OSError("manifest input must have exactly one link")
        if before.st_size > maximum:
            raise OverflowError(f"byte limit {maximum} exceeded")
        file_descriptor = os.open(
            leaf_name,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0) | nofollow,
            dir_fd=current_descriptor,
        )
        opened = os.fstat(file_descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or _file_identity(opened) != _file_identity(before):
            raise OSError("manifest input changed while opening")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(file_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise OSError("manifest input was truncated while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_descriptor, 1):
            raise OverflowError(f"byte limit {maximum} exceeded")
        after = os.fstat(file_descriptor)
        path_after = os.stat(leaf_name, dir_fd=current_descriptor, follow_symlinks=False)
        if (
            _file_identity(after) != _file_identity(opened)
            or after.st_nlink != 1
            or _file_identity(path_after) != _file_identity(opened)
        ):
            raise OSError("manifest input changed while reading")
        for parent_descriptor, component, child_descriptor, opened_directory in directory_records:
            descriptor_after = os.fstat(child_descriptor)
            path_directory_after = os.stat(
                component,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if _file_identity(descriptor_after) != _file_identity(opened_directory) or _file_identity(
                path_directory_after
            ) != _file_identity(opened_directory):
                raise OSError("manifest directory changed while reading")
        root_after = os.fstat(root_descriptor)
        root_path_after = os.stat(os.sep, follow_symlinks=False)
        if _file_identity(root_after) != _file_identity(root_opened) or _file_identity(
            root_path_after
        ) != _file_identity(root_opened):
            raise OSError("manifest root changed while reading")
        return b"".join(chunks)
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        for directory_descriptor in reversed(directory_descriptors):
            os.close(directory_descriptor)


def _decode_xml_bytes(value: bytes) -> str:
    if value.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encoding = "utf-32"
    elif value.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    elif value[:4] in {b"<\x00?\x00", b"\x00<\x00?"}:
        encoding = "utf-16-le" if value.startswith(b"<\x00") else "utf-16-be"
    else:
        declaration = re.search(
            rb"<\?xml[^>]{0,200}\bencoding\s*=\s*['\"]([A-Za-z0-9._-]+)['\"]",
            value[:256],
            re.IGNORECASE,
        )
        encoding = declaration.group(1).decode("ascii") if declaration else "utf-8-sig"
    return value.decode(encoding, errors="strict")


def _xml_shape(root: ET.Element) -> tuple[int, int]:
    count = 0
    maximum_depth = 0
    pending = [(root, 1)]
    while pending:
        element, depth = pending.pop()
        count += 1
        maximum_depth = max(maximum_depth, depth)
        pending.extend((child, depth + 1) for child in element)
    return count, maximum_depth


def _feed_authors(element: ET.Element, *, atom: bool) -> list[dict[str, str]]:
    authors: list[dict[str, str]] = []
    for child in element:
        local_name = _xml_local_name(child.tag)
        namespace = _xml_namespace(child.tag)
        atom_author = atom and local_name == "author" and namespace == ATOM_NAMESPACE
        rss_author = not atom and (
            (local_name == "author" and namespace == "") or (local_name == "creator" and namespace == RSS_DC_NAMESPACE)
        )
        if not (atom_author or rss_author):
            continue
        nested = {
            _xml_local_name(item.tag): normalize_text("".join(item.itertext()))
            for item in child
            if not atom or _xml_namespace(item.tag) == ATOM_NAMESPACE
        }
        authors.append(
            {
                "email": nested.get("email", ""),
                "name": nested.get("name", "") or normalize_text("".join(child.itertext())),
                "uri": nested.get("uri", ""),
            }
        )
    return authors


def _feed_categories(element: ET.Element, *, atom: bool) -> list[dict[str, str]]:
    return [
        {
            "label": normalize_text(child.attrib.get("label", "")),
            "scheme": normalize_text(child.attrib.get("scheme", "") or child.attrib.get("domain", "")),
            "term": normalize_text(child.attrib.get("term", "") or "".join(child.itertext())),
        }
        for child in element
        if _xml_local_name(child.tag) == "category" and _xml_namespace(child.tag) == (ATOM_NAMESPACE if atom else "")
    ]


def _feed_enclosures(
    element: ET.Element,
    *,
    atom: bool,
    document_path: str,
    origin: str,
) -> list[dict[str, str]]:
    enclosures: list[dict[str, str]] = []
    for child in element:
        local_name = _xml_local_name(child.tag)
        namespace = _xml_namespace(child.tag)
        if not atom and local_name == "enclosure" and namespace == "":
            raw_url = child.attrib.get("url", "")
        elif (
            atom
            and local_name == "link"
            and namespace == ATOM_NAMESPACE
            and child.attrib.get("rel", "").lower() == "enclosure"
        ):
            raw_url = child.attrib.get("href", "")
        else:
            continue
        enclosures.append(
            {
                "length": child.attrib.get("length", ""),
                "type": child.attrib.get("type", ""),
                "url": normalize_non_html_reference(raw_url, route=document_path, origin=origin) if raw_url else "",
            }
        )
    return enclosures


def _feed_metadata(
    container: ET.Element,
    *,
    atom: bool,
    document_path: str,
    origin: str,
    namespace: str,
    version: str,
) -> dict[str, Any]:
    child_namespace = ATOM_NAMESPACE if atom else ""
    links: list[dict[str, str]] = []
    for child in container:
        child_link_namespace = _xml_namespace(child.tag)
        atom_link = child_link_namespace == ATOM_NAMESPACE
        rss_channel_link = not atom and child_link_namespace == ""
        if _xml_local_name(child.tag) != "link" or not (atom_link or rss_channel_link):
            continue
        if atom and not atom_link:
            continue
        raw_url = child.attrib.get("href", "") if atom_link else normalize_text("".join(child.itertext()))
        if not raw_url:
            continue
        links.append(
            {
                "href": normalize_non_html_reference(
                    raw_url,
                    route=document_path,
                    origin=origin,
                    route_like=True,
                ),
                "hreflang": child.attrib.get("hreflang", ""),
                "rel": child.attrib.get("rel", "alternate" if atom_link else "channel").lower(),
                "source": "atom-link" if atom_link else "rss-channel-link",
                "title": normalize_text(child.attrib.get("title", "")),
                "type": child.attrib.get("type", "").lower(),
            }
        )
    alternate = next((item["href"] for item in links if item["rel"] in {"alternate", "channel"}), "")
    return {
        "authors": _feed_authors(container, atom=atom),
        "categories": _feed_categories(container, atom=atom),
        "description": _direct_child_text(container, "description", child_namespace),
        "generator": _direct_child_text(container, "generator", child_namespace),
        "id": _direct_child_text(container, "id", child_namespace),
        "language": _direct_child_text(container, "language", child_namespace),
        "links": links,
        "namespace": namespace,
        "rights": _direct_child_text(container, "rights" if atom else "copyright", child_namespace),
        "subtitle": _direct_child_text(container, "subtitle", child_namespace),
        "title": _direct_child_text(container, "title", child_namespace),
        "updated": _direct_child_text(container, "updated" if atom else "lastbuilddate", child_namespace)
        or _direct_child_text(container, "pubdate", child_namespace),
        "url": alternate,
        "version": version,
        "xml_lang": normalize_text(container.attrib.get(f"{{{XML_NAMESPACE}}}lang", "")) if atom else "",
    }


def _inspect_xml_feature_unchecked(
    root_path: Path,
    path: Path,
    relative: str,
    *,
    origin: str,
    limits: dict[str, int],
    verifier: StageBoundaryVerifier,
) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """Classify and validate RSS/Atom or sitemap XML structural evidence."""
    filename = path.name.lower()
    expected = (
        "sitemap" if "sitemap" in filename else "rss" if filename in {"atom.xml", "feed.xml", "rss.xml"} else None
    )
    try:
        xml_bytes = verifier.read_bytes(path, limits["xml_bytes"])
        xml_text = _decode_xml_bytes(xml_bytes)
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml_text, re.IGNORECASE):
            raise ET.ParseError("DTD and entity declarations are forbidden")
        root = ET.fromstring(xml_text)  # noqa: S314
        element_count, maximum_depth = _xml_shape(root)
        if element_count > limits["xml_entries"]:
            raise OverflowError(f"element limit {limits['xml_entries']} exceeded")
        if maximum_depth > limits["xml_depth"]:
            raise OverflowError(f"depth limit {limits['xml_depth']} exceeded")
    except (MemoryError, OverflowError) as exc:
        if expected:
            return (
                expected,
                None,
                [
                    {
                        "code": "limit-exceeded",
                        "detail": str(exc) or "bounded XML parse exhausted memory",
                        "path": f"/{relative}",
                        "resource": "xml",
                    }
                ],
            )
        return None, None, []
    except SafeFileError as exc:
        if expected and exc.code == "limit-exceeded":
            return (
                expected,
                None,
                [
                    {
                        "code": "limit-exceeded",
                        "detail": str(exc),
                        "path": f"/{relative}",
                        "resource": "xml",
                    }
                ],
            )
        if expected:
            return expected, None, [{"code": f"invalid-{expected}", "path": f"/{relative}", "detail": str(exc)}]
        return None, None, []
    except (LookupError, UnicodeDecodeError):
        if expected:
            return (
                expected,
                None,
                [{"code": f"invalid-{expected}", "path": f"/{relative}", "detail": "XML decoding failed"}],
            )
        return None, None, []
    except ET.ParseError:
        if expected:
            return (
                expected,
                None,
                [{"code": f"invalid-{expected}", "path": f"/{relative}", "detail": "XML parsing failed"}],
            )
        return None, None, []
    except (OSError, ValueError):
        if expected:
            return (
                expected,
                None,
                [{"code": f"invalid-{expected}", "path": f"/{relative}", "detail": "XML processing failed"}],
            )
        return None, None, []

    root_name = _xml_local_name(root.tag)
    root_namespace = _xml_namespace(root.tag)
    document_path = normalize_route(f"/{relative}")
    container = root
    version = ""
    if root_name == "feed" and root_namespace == ATOM_NAMESPACE:
        kind = "rss"
        entry_name = "entry"
        entry_namespace = ATOM_NAMESPACE
        atom = True
    elif root_name == "rss" and root_namespace == "" and root.attrib.get("version", "") == "2.0":
        kind = "rss"
        entry_name = "item"
        entry_namespace = ""
        atom = False
        version = "2.0"
        channels = _xml_children(root, "channel", "")
        if len(channels) != 1:
            return (
                kind,
                None,
                [
                    {
                        "code": "invalid-rss",
                        "path": document_path,
                        "detail": "RSS 2.0 must contain exactly one unnamespaced channel",
                    }
                ],
            )
        container = channels[0]
    elif root_name in {"sitemapindex", "urlset"} and root_namespace == SITEMAP_NAMESPACE:
        kind = "sitemap"
        entry_name = "sitemap" if root_name == "sitemapindex" else "url"
        entry_namespace = SITEMAP_NAMESPACE
        atom = False
    elif expected:
        return (
            expected,
            None,
            [
                {
                    "code": f"invalid-{expected}",
                    "path": document_path,
                    "detail": "unexpected XML root element or namespace",
                }
            ],
        )
    else:
        return None, None, []

    if kind == "rss" and any(XML_BASE_ATTRIBUTE in element.attrib for element in root.iter()):
        return (
            kind,
            None,
            [
                {
                    "code": "invalid-rss",
                    "path": document_path,
                    "detail": "Feed xml:base is unsupported by the closed URL policy",
                }
            ],
        )

    elements = _xml_children(container, entry_name, entry_namespace)
    nested_entries = [
        element
        for element in root.iter()
        if _xml_local_name(element.tag) == entry_name and _xml_namespace(element.tag) == entry_namespace
    ]
    if len(elements) != len(nested_entries):
        return (
            kind,
            None,
            [
                {
                    "code": f"invalid-{kind}",
                    "path": document_path,
                    "detail": "entry elements must be direct children of the exact feed/sitemap container",
                }
            ],
        )
    if not elements:
        return kind, None, [{"code": f"empty-{kind}", "path": document_path}]

    canonical_budget: _XmlCanonicalBudget | None = None
    metadata_canonical_digest = ""
    metadata_canonical_elements: list[dict[str, str]] = []
    if kind == "rss":
        canonical_budget = _XmlCanonicalBudget(
            maximum_nodes=limits["manifest_nodes"],
            maximum_bytes=limits["manifest_bytes"],
        )
        excluded_entry_ids = frozenset(id(element) for element in elements)
        try:
            metadata_canonical_digest = canonical_budget.digest(
                container,
                excluded_direct_ids=excluded_entry_ids,
            )
            for metadata_element in container:
                if id(metadata_element) in excluded_entry_ids:
                    continue
                metadata_canonical_elements.append(
                    {
                        "digest": canonical_budget.digest(metadata_element),
                        "tag_digest": canonical_digest(metadata_element.tag),
                    }
                )
        except (MemoryError, OverflowError, UnicodeError) as exc:
            return (
                kind,
                None,
                [
                    {
                        "code": "limit-exceeded",
                        "detail": str(exc) or "canonical feed metadata evidence exhausted its bound",
                        "path": document_path,
                        "resource": "xml",
                    }
                ],
            )

    entries: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for index, element in enumerate(elements):
        element_name = _xml_local_name(element.tag)
        entry_canonical_digest = ""
        effective_xml_lang = ""
        if kind == "rss":
            assert canonical_budget is not None
            if atom:
                effective_xml_lang = normalize_text(
                    element.attrib.get(f"{{{XML_NAMESPACE}}}lang", "")
                    or container.attrib.get(f"{{{XML_NAMESPACE}}}lang", "")
                )
            try:
                entry_canonical_digest = canonical_budget.digest(
                    element,
                    context=(("effective_xml_lang", effective_xml_lang),),
                )
            except (MemoryError, OverflowError, UnicodeError) as exc:
                return (
                    kind,
                    None,
                    [
                        {
                            "code": "limit-exceeded",
                            "detail": str(exc) or "canonical feed entry evidence exhausted its bound",
                            "path": document_path,
                            "resource": "xml",
                        }
                    ],
                )
        if kind == "sitemap":
            raw_url = _direct_child_text(element, "loc", SITEMAP_NAMESPACE)
            missing = [] if raw_url else ["loc"]
            entry = (
                {
                    "changefreq": _direct_child_text(element, "changefreq", SITEMAP_NAMESPACE),
                    "lastmod": _direct_child_text(element, "lastmod", SITEMAP_NAMESPACE),
                    "priority": _direct_child_text(element, "priority", SITEMAP_NAMESPACE),
                    "url": normalize_non_html_reference(
                        raw_url,
                        route=document_path,
                        origin=origin,
                        route_like=True,
                    ),
                }
                if raw_url
                else {}
            )
        elif element_name == "entry":
            raw_url = _atom_entry_link(element)
            entry = {
                "authors": _feed_authors(element, atom=True),
                "categories": _feed_categories(element, atom=True),
                "content": _direct_child_text(element, "content", ATOM_NAMESPACE),
                "canonical_digest": entry_canonical_digest,
                "description": _direct_child_text(element, "description", ATOM_NAMESPACE),
                "enclosures": _feed_enclosures(
                    element,
                    atom=True,
                    document_path=document_path,
                    origin=origin,
                ),
                "effective_xml_lang": effective_xml_lang,
                "guid": "",
                "id": _direct_child_text(element, "id", ATOM_NAMESPACE),
                "published": _direct_child_text(element, "published", ATOM_NAMESPACE),
                "summary": _direct_child_text(element, "summary", ATOM_NAMESPACE),
                "title": _direct_child_text(element, "title", ATOM_NAMESPACE),
                "updated": _direct_child_text(element, "updated", ATOM_NAMESPACE),
                "url": normalize_non_html_reference(raw_url, route=document_path, origin=origin, route_like=True)
                if raw_url
                else "",
            }
            missing = sorted(key for key in ("id", "title", "updated", "url") if not entry[key])
        else:
            raw_url = _direct_child_text(element, "link")
            entry = {
                "authors": _feed_authors(element, atom=False),
                "canonical_digest": entry_canonical_digest,
                "categories": _feed_categories(element, atom=False),
                "content": _direct_child_text(element, "encoded", RSS_CONTENT_NAMESPACE),
                "description": _direct_child_text(element, "description"),
                "enclosures": _feed_enclosures(
                    element,
                    atom=False,
                    document_path=document_path,
                    origin=origin,
                ),
                "effective_xml_lang": effective_xml_lang,
                "guid": _direct_child_text(element, "guid"),
                "id": "",
                "published": _direct_child_text(element, "pubdate"),
                "summary": _direct_child_text(element, "summary"),
                "title": _direct_child_text(element, "title"),
                "updated": _direct_child_text(element, "updated"),
                "url": normalize_non_html_reference(raw_url, route=document_path, origin=origin, route_like=True)
                if raw_url
                else "",
            }
            missing = sorted(key for key in ("title", "url") if not entry[key])
        if missing:
            findings.append(
                {
                    "code": f"invalid-{kind}-entry",
                    "path": document_path,
                    "entry_index": index,
                    "missing": missing,
                }
            )
        else:
            entries.append(entry)
    evidence = None
    if entries:
        metadata = (
            _feed_metadata(
                container,
                atom=atom,
                document_path=document_path,
                origin=origin,
                namespace=root_namespace,
                version=version,
            )
            if kind == "rss"
            else {
                "authors": [],
                "categories": [],
                "description": "",
                "generator": "",
                "id": "",
                "language": "",
                "links": [],
                "namespace": root_namespace,
                "rights": "",
                "subtitle": "",
                "title": "",
                "updated": "",
                "url": "",
                "version": "",
                "xml_lang": "",
            }
        )
        if kind == "rss":
            metadata["canonical_digest"] = metadata_canonical_digest
            metadata["canonical_elements"] = metadata_canonical_elements
            required_metadata = ("id", "title", "updated") if atom else ("description", "title", "url")
            missing_metadata = [name for name in required_metadata if not metadata[name]]
            if missing_metadata:
                findings.append(
                    {
                        "code": "invalid-rss",
                        "detail": f"missing required feed metadata: {', '.join(missing_metadata)}",
                        "path": document_path,
                    }
                )
        evidence = {
            "path": document_path,
            "root": root_name,
            "entries": entries,
            "metadata": metadata,
        }
    return kind, evidence, findings


def _inspect_xml_feature(
    root_path: Path,
    path: Path,
    relative: str,
    *,
    origin: str,
    limits: dict[str, int],
    verifier: StageBoundaryVerifier,
) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """Turn every post-parse URL normalization failure into bounded evidence."""
    try:
        return _inspect_xml_feature_unchecked(
            root_path,
            path,
            relative,
            origin=origin,
            limits=limits,
            verifier=verifier,
        )
    except CredentialBearingUrlError as exc:
        return (
            None,
            None,
            [
                {
                    "code": "credential-bearing-url",
                    "path": normalize_route(f"/{relative}"),
                    "reference_digest": exc.reference_digest,
                    "resource": "xml",
                }
            ],
        )
    except ValueError:
        return (
            None,
            None,
            [
                {
                    "code": "invalid-xml-url",
                    "detail": "URL normalization failed",
                    "path": normalize_route(f"/{relative}"),
                }
            ],
        )


def _inspect_robots(
    root: Path,
    path: Path,
    *,
    origin: str,
    limits: dict[str, int],
    verifier: StageBoundaryVerifier,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        raw_lines = verifier.read_bytes(path, limits["robots_bytes"]).decode("utf-8", errors="strict").splitlines()
    except SafeFileError as exc:
        if exc.code == "limit-exceeded":
            return None, {
                "code": "limit-exceeded",
                "detail": str(exc),
                "path": "/robots.txt",
                "resource": "robots",
            }
        return None, {"code": "invalid-robots", "path": "/robots.txt", "detail": str(exc)}
    except MemoryError:
        return None, {
            "code": "limit-exceeded",
            "detail": "bounded robots parse exhausted memory",
            "path": "/robots.txt",
            "resource": "robots",
        }
    except (OSError, UnicodeDecodeError) as exc:
        return None, {"code": "invalid-robots", "path": "/robots.txt", "detail": str(exc)}
    lines = [line.split("#", 1)[0].strip() for line in raw_lines]
    lines = [line for line in lines if line]
    if not lines:
        return None, {"code": "empty-robots", "path": "/robots.txt"}

    groups: list[dict[str, Any]] = []
    user_agents: list[str] = []
    directives: list[dict[str, str]] = []
    sitemaps: list[str] = []
    errors: list[str] = []

    def finish_group() -> None:
        nonlocal user_agents, directives
        if user_agents:
            if directives:
                groups.append({"user_agents": user_agents, "directives": directives})
            else:
                errors.append("user-agent group has no directives")
        user_agents = []
        directives = []

    for line_number, line in enumerate(lines, 1):
        if ":" not in line:
            errors.append(f"line {line_number} has no colon")
            continue
        raw_name, raw_value = line.split(":", 1)
        name = raw_name.strip().lower()
        value = raw_value.strip()
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            errors.append(f"line {line_number} has an invalid directive name")
            continue
        if name == "user-agent":
            if directives:
                finish_group()
            if not value:
                errors.append(f"line {line_number} has an empty user-agent")
            else:
                user_agents.append(value)
            continue
        if name == "sitemap":
            if not value:
                errors.append(f"line {line_number} has an empty sitemap")
                continue
            try:
                normalized = normalize_non_html_reference(
                    value,
                    route="/robots.txt",
                    origin=origin,
                    route_like=True,
                )
            except CredentialBearingUrlError as exc:
                return None, {
                    "code": "credential-bearing-url",
                    "path": "/robots.txt",
                    "reference_digest": exc.reference_digest,
                    "resource": "robots",
                }
            except ValueError:
                errors.append(f"line {line_number} sitemap URL is invalid")
                continue
            parts = urlsplit(normalized)
            if parts.scheme or parts.netloc or parts.query or parts.fragment:
                errors.append(f"line {line_number} sitemap is not a same-origin canonical path")
            else:
                sitemaps.append(normalized)
            continue
        if not user_agents:
            errors.append(f"line {line_number} directive appears before a user-agent")
            continue
        if not value and name not in {"allow", "disallow"}:
            errors.append(f"line {line_number} has an empty {name} value")
            continue
        directives.append({"name": name, "value": value})
    finish_group()

    if not groups:
        errors.append("no nonempty user-agent groups")
    if not sitemaps:
        errors.append("no valid Sitemap directive")
    if errors:
        return None, {
            "code": "invalid-robots",
            "path": "/robots.txt",
            "detail": "; ".join(sorted(set(errors))),
        }
    return {"path": "/robots.txt", "groups": groups, "sitemaps": sitemaps}, None


def _canonical_indexable_routes(routes: dict[str, dict[str, Any]], aliases: dict[str, dict[str, Any]]) -> set[str]:
    canonical_routes: set[str] = set()
    for route, page in routes.items():
        metadata = page.get("metadata", {})
        canonical = metadata.get("canonical", "")
        parts = urlsplit(canonical)
        if (
            canonical
            and not parts.scheme
            and not parts.netloc
            and not parts.query
            and not parts.fragment
            and normalize_route(parts.path) == route
            and route not in aliases
            and not metadata.get("noindex", False)
        ):
            canonical_routes.add(canonical)
    return canonical_routes


def _alias_integrity_findings(
    routes: dict[str, dict[str, Any]], aliases: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    target_routes: dict[str, str] = {}
    for alias_route, alias in aliases.items():
        target = alias["target"]
        parts = urlsplit(target)
        target_route = "" if parts.scheme or parts.netloc else normalize_route(parts.path)
        target_routes[alias_route] = target_route
        violations: set[str] = set()
        if alias["canonical"] != target:
            violations.add("canonical-mismatch")
        if not alias["noindex"]:
            violations.add("missing-noindex")
        if not alias["follow"]:
            violations.add("missing-follow")
        if not target_route:
            violations.add("nonlocal-target")
        elif target_route == alias_route:
            violations.add("self-target")
        elif target_route not in routes:
            violations.add("alias-or-dangling-target" if target_route in aliases else "dangling-target")
        else:
            canonical = routes[target_route]["metadata"]["canonical"]
            canonical_parts = urlsplit(canonical)
            if (
                not canonical
                or canonical_parts.scheme
                or canonical_parts.netloc
                or normalize_route(canonical_parts.path) != target_route
            ):
                violations.add("target-not-canonical-route")
        if violations:
            findings.append({"code": "invalid-alias", "route": alias_route, "violations": sorted(violations)})

    cycle_routes: set[str] = set()
    for start in aliases:
        order: list[str] = []
        seen: dict[str, int] = {}
        current = start
        while current in aliases and current not in seen:
            seen[current] = len(order)
            order.append(current)
            current = target_routes.get(current, "")
        if current in seen:
            cycle_routes.update(order[seen[current] :])
    for route in sorted(cycle_routes):
        existing = next((finding for finding in findings if finding.get("route") == route), None)
        if existing:
            existing["violations"] = sorted({*existing["violations"], "cycle"})
        else:
            findings.append({"code": "invalid-alias", "route": route, "violations": ["cycle"]})
    return sorted(findings, key=canonical_json)


def _rss_route_findings(documents: list[dict[str, Any]], canonical_indexable_routes: set[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for document in documents:
        invalid = sorted(
            {
                entry.get("url", "")
                for entry in document.get("entries", [])
                if entry.get("url", "") not in canonical_indexable_routes
            }
        )
        if invalid:
            findings.append({"code": "rss-route-mismatch", "path": document["path"], "invalid": invalid})
    return findings


def _canonical_directed_cycle(nodes: list[str]) -> tuple[str, ...]:
    """Return the lexicographically least directed rotation in linear time."""

    if not nodes:
        raise ValueError("cycle must contain at least one node")
    if len(nodes) == 1:
        return nodes[0], nodes[0]
    doubled = nodes + nodes
    first = 0
    second = 1
    offset = 0
    length = len(nodes)
    while first < length and second < length and offset < length:
        left = doubled[first + offset]
        right = doubled[second + offset]
        if left == right:
            offset += 1
            continue
        if left > right:
            first += offset + 1
            if first == second:
                first += 1
        else:
            second += offset + 1
            if first == second:
                second += 1
        offset = 0
    start = min(first, second)
    rotated = nodes[start:] + nodes[:start]
    return tuple([*rotated, rotated[0]])


class _BoundedCycleEvidence:
    """Charge every backedge attempt before canonicalization or deduplication."""

    def __init__(self, *, maximum_cycles: int, maximum_nodes: int, maximum_bytes: int) -> None:
        self.maximum_cycles = maximum_cycles
        self.maximum_nodes = maximum_nodes
        self.maximum_bytes = maximum_bytes
        self.cycles: set[tuple[str, ...]] = set()
        self.attempts = 0
        self.nodes = 0
        self.bytes = 0
        self.exhausted = False

    def add_from_stack(self, path_stack: list[str], start: int) -> None:
        if self.exhausted:
            return
        cycle_node_count = len(path_stack) - start + 1
        if self.attempts >= self.maximum_cycles or self.nodes + cycle_node_count > self.maximum_nodes:
            self.exhausted = True
            return
        try:
            cycle_byte_count = sum(len(path_stack[index].encode("utf-8")) for index in range(start, len(path_stack)))
            cycle_byte_count += len(path_stack[start].encode("utf-8"))
        except (MemoryError, UnicodeError):
            self.exhausted = True
            return
        if self.bytes + cycle_byte_count > self.maximum_bytes:
            self.exhausted = True
            return
        self.attempts += 1
        self.nodes += cycle_node_count
        self.bytes += cycle_byte_count
        try:
            cycle = _canonical_directed_cycle(path_stack[start:])
        except MemoryError:
            self.exhausted = True
            return
        if cycle in self.cycles:
            return
        self.cycles.add(cycle)


def _sitemap_closure(
    documents: list[dict[str, Any]],
    robots_documents: list[dict[str, Any]],
    *,
    limits: dict[str, int],
) -> tuple[set[str], list[dict[str, Any]]]:
    """Return the bounded local sitemap closure and exact graph findings."""
    document_map = {document["path"]: document for document in documents}
    findings: list[dict[str, Any]] = []
    adjacency_sets: dict[str, set[str]] = {path: set() for path in document_map}
    referenced_paths: set[str] = set()
    edge_count = 0
    edge_limit_reported = False
    for source_path, document in sorted(document_map.items()):
        if document["root"] != "sitemapindex":
            continue
        for entry in document["entries"]:
            reference = entry["url"]
            reference_digest = canonical_digest(reference)
            parts = urlsplit(reference)
            target_path = parts.path if not (parts.scheme or parts.netloc or parts.query or parts.fragment) else ""
            if not target_path or target_path != normalize_route(target_path):
                findings.append(
                    {
                        "code": "sitemap-child-invalid",
                        "path": source_path,
                        "reason": "nonlocal",
                        "reference_digest": reference_digest,
                        "target_path": "",
                    }
                )
                continue
            referenced_paths.add(target_path)
            target = document_map.get(target_path)
            if target is None or target.get("root") not in {"sitemapindex", "urlset"}:
                findings.append(
                    {
                        "code": "sitemap-child-invalid",
                        "path": source_path,
                        "reason": "missing-or-invalid-child",
                        "reference_digest": reference_digest,
                        "target_path": target_path,
                    }
                )
                continue
            edge_count += 1
            if edge_count > limits["xml_entries"]:
                if not edge_limit_reported:
                    findings.append(
                        {
                            "code": "limit-exceeded",
                            "detail": f"sitemap graph edge limit {limits['xml_entries']} exceeded",
                            "path": source_path,
                            "resource": "xml",
                        }
                    )
                    edge_limit_reported = True
                continue
            adjacency_sets[source_path].add(target_path)

    adjacency = {path: sorted(targets) for path, targets in adjacency_sets.items()}

    declared_roots = sorted({sitemap for document in robots_documents for sitemap in document.get("sitemaps", [])})
    index_paths = {path for path, document in document_map.items() if document["root"] == "sitemapindex"}
    if declared_roots:
        roots = declared_roots
    elif index_paths:
        roots = sorted(index_paths - referenced_paths) or sorted(index_paths)
    else:
        roots = sorted(document_map)

    reachable: set[str] = set()
    pending = deque((path, 0) for path in roots)
    depth_reported: set[str] = set()
    while pending:
        current, depth = pending.popleft()
        if depth > limits["xml_depth"]:
            if current not in depth_reported:
                findings.append(
                    {
                        "code": "limit-exceeded",
                        "detail": f"sitemap graph depth limit {limits['xml_depth']} exceeded",
                        "path": current,
                        "resource": "xml",
                    }
                )
                depth_reported.add(current)
            continue
        if current in reachable or current not in document_map:
            continue
        if len(reachable) >= limits["xml_entries"]:
            findings.append(
                {
                    "code": "limit-exceeded",
                    "detail": f"sitemap graph document limit {limits['xml_entries']} exceeded",
                    "path": current,
                    "resource": "xml",
                }
            )
            break
        reachable.add(current)
        pending.extend((target, depth + 1) for target in adjacency[current])

    state: dict[str, int] = {}
    cycles = _BoundedCycleEvidence(
        maximum_cycles=min(limits["xml_entries"], limits["manifest_nodes"]),
        maximum_nodes=limits["manifest_nodes"],
        maximum_bytes=limits["manifest_bytes"],
    )
    for start in sorted(document_map):
        if state.get(start, 0) != 0:
            continue
        state[start] = 1
        path_stack = [start]
        path_positions = {start: 0}
        frames = [(start, iter(adjacency[start]))]
        while frames:
            current, targets = frames[-1]
            try:
                target = next(targets)
            except StopIteration:
                state[current] = 2
                frames.pop()
                path_positions.pop(path_stack.pop(), None)
                continue
            if state.get(target, 0) == 0:
                state[target] = 1
                path_positions[target] = len(path_stack)
                path_stack.append(target)
                frames.append((target, iter(adjacency[target])))
            elif state.get(target) == 1:
                cycles.add_from_stack(path_stack, path_positions[target])
    findings.extend({"code": "sitemap-cycle", "paths": list(cycle)} for cycle in sorted(cycles.cycles))
    if cycles.exhausted:
        findings.append(
            {
                "code": "limit-exceeded",
                "detail": "sitemap cycle evidence limit exceeded",
                "path": "/",
                "resource": "xml",
            }
        )
    findings.extend({"code": "sitemap-orphan", "path": path} for path in sorted(set(document_map) - reachable))
    return reachable, sorted(findings, key=canonical_json)


def _sitemap_route_findings(
    documents: list[dict[str, Any]],
    canonical_indexable_routes: set[str],
    robots_documents: list[dict[str, Any]],
    *,
    limits: dict[str, int],
) -> list[dict[str, Any]]:
    if not documents:
        return []
    reachable, _findings = _sitemap_closure(documents, robots_documents, limits=limits)
    actual_urls = {
        entry["url"]
        for document in documents
        if document["path"] in reachable and document["root"] == "urlset"
        for entry in document["entries"]
    }
    if actual_urls == canonical_indexable_routes:
        return []
    return [
        {
            "code": "sitemap-route-mismatch",
            "path": min(document["path"] for document in documents),
            "missing": sorted(canonical_indexable_routes - actual_urls),
            "extra": sorted(actual_urls - canonical_indexable_routes),
        }
    ]


def _robots_sitemap_findings(
    robots_documents: list[dict[str, Any]], sitemap_documents: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    available = {document["path"] for document in sitemap_documents}
    findings: list[dict[str, Any]] = []
    for document in robots_documents:
        invalid = sorted(set(document["sitemaps"]) - available)
        if invalid:
            findings.append({"code": "robots-sitemap-mismatch", "path": document["path"], "invalid": invalid})
    return findings


def _search_asset_kind(relative: str) -> str | None:
    lowered = relative.lower()
    name = Path(lowered).name
    if name.startswith("wasm.") and name.endswith(".pagefind"):
        return "runtime"
    if ("pagefind" in lowered or "search" in name) and Path(name).suffix in {".js", ".mjs", ".wasm"}:
        return "runtime"
    if (
        name in {"contentindex.json", "pagefind-entry.json", "search-index.json", "search_index.json"}
        or name.endswith((".pf_fragment", ".pf_index", ".pf_meta"))
        or ("pagefind" in lowered and "index" in name)
    ):
        return "index"
    return None


def _inspect_search_asset(
    root: Path,
    path: Path,
    relative: str,
    kind: str,
    *,
    available_paths: dict[str, Path],
    limits: dict[str, int],
    verifier: StageBoundaryVerifier,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    try:
        search_bytes = verifier.read_bytes(
            path,
            limits["json_bytes"] if path.suffix.lower() == ".json" else limits["asset_bytes"],
        )
    except MemoryError:
        return False, None, "bounded search-index parse exhausted memory"
    except OSError as exc:
        return False, None, str(exc)
    if not search_bytes:
        return False, None, None
    if kind == "index" and path.suffix.lower() == ".json":
        try:
            value = json.loads(
                search_bytes.decode("utf-8", errors="strict"),
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
            )
        except (MemoryError, RecursionError):
            return False, None, f"depth limit {limits['json_depth']} exceeded"
        except (UnicodeDecodeError, ValueError):
            return False, None, None
        try:
            _json_shape(
                value,
                maximum_depth=limits["json_depth"],
                maximum_nodes=limits["json_nodes"],
            )
        except ValueError as exc:
            return False, None, str(exc)
        name = path.name.lower()
        if name == "pagefind-entry.json":
            allowed_entry_keys = {"languages", "version"}
            if isinstance(value, dict) and "include_characters" in value:
                allowed_entry_keys.add("include_characters")
            if (
                not isinstance(value, dict)
                or set(value) != allowed_entry_keys
                or not isinstance(value["version"], str)
                or not value["version"]
                or len(value["version"].encode()) > 64
                or not isinstance(value.get("languages"), dict)
                or not value["languages"]
            ):
                return False, None, None
            include_characters = value.get("include_characters", [])
            if (
                not isinstance(include_characters, list)
                or len(include_characters) > 256
                or any(
                    not isinstance(character, str)
                    or not character
                    or len(character.encode("utf-8")) > 16
                    or any(ord(codepoint) < 32 or ord(codepoint) == 127 for codepoint in character)
                    for character in include_characters
                )
            ):
                return False, None, None
            languages: list[str] = []
            descriptors: list[dict[str, Any]] = []
            base_directory = Path(relative).parent.as_posix()
            for language, descriptor in value["languages"].items():
                if (
                    not isinstance(language, str)
                    or not language
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", language)
                    or not isinstance(descriptor, dict)
                    or set(descriptor) != {"hash", "page_count", "wasm"}
                    or not isinstance(descriptor.get("hash"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", descriptor["hash"])
                    or type(descriptor.get("page_count")) is not int
                    or descriptor["page_count"] < 1
                    or not isinstance(descriptor.get("wasm"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", descriptor["wasm"])
                ):
                    return False, None, None
                languages.append(language)
                metadata_names = {
                    f"pagefind.{descriptor['hash']}.pf_meta",
                    f"pagefind.{language}_{descriptor['hash']}.pf_meta",
                }
                language_shard = re.compile(rf"{re.escape(language)}_[A-Za-z0-9_-]{{6,128}}\.(pf_index|pf_fragment)\Z")
                matching_shards = sorted(
                    (
                        public_path,
                        shard_path,
                    )
                    for public_path, shard_path in available_paths.items()
                    if (
                        (
                            Path(public_path.lstrip("/")).parent.as_posix() == base_directory
                            and Path(public_path).name in metadata_names
                        )
                        or (
                            Path(public_path.lstrip("/")).parent.as_posix()
                            in {f"{base_directory}/index", f"{base_directory}/fragment"}
                            and language_shard.fullmatch(Path(public_path).name)
                        )
                        or (
                            Path(public_path.lstrip("/")).parent.as_posix() == base_directory
                            and Path(public_path).name == f"pagefind.{language}_{descriptor['hash']}.pf_index"
                        )
                    )
                )
                formats = [Path(public_path).suffix.lower() for public_path, _path in matching_shards]
                if formats.count(".pf_meta") != 1 or ".pf_index" not in formats:
                    return False, None, None
                shards: list[dict[str, Any]] = []
                for public_path, shard_path in matching_shards:
                    try:
                        size, digest = verifier.sha256_file(shard_path, limits["asset_bytes"])
                    except SafeFileError as exc:
                        return False, None, str(exc)
                    if size < 1:
                        return False, None, None
                    shards.append(
                        {
                            "format": Path(public_path).suffix.lower().lstrip("."),
                            "path": public_path,
                            "sha256": digest,
                            "size": size,
                        }
                    )
                wasm_relative = (Path(base_directory) / f"wasm.{descriptor['wasm']}.pagefind").as_posix()
                wasm_public_path = f"/{wasm_relative}"
                wasm_path = available_paths.get(wasm_public_path)
                if wasm_path is None:
                    return False, None, None
                try:
                    wasm_size, wasm_digest = verifier.sha256_file(wasm_path, limits["asset_bytes"])
                except SafeFileError as exc:
                    return False, None, str(exc)
                if wasm_size < 1:
                    return False, None, None
                descriptors.append(
                    {
                        "descriptor_digest": canonical_digest({"language": language, **descriptor}),
                        "hash": descriptor["hash"],
                        "language": language,
                        "page_count": descriptor["page_count"],
                        "shards": shards,
                        "wasm": descriptor["wasm"],
                        "wasm_asset": {
                            "path": wasm_public_path,
                            "sha256": wasm_digest,
                            "size": wasm_size,
                        },
                    }
                )
            return (
                True,
                {
                    "path": f"/{relative}",
                    "format": "pagefind-entry",
                    "entry_count": len(languages),
                    "languages": sorted(languages),
                    "descriptors": sorted(descriptors, key=canonical_json),
                },
                None,
            )
        if isinstance(value, dict) and value and all(isinstance(item, (dict, list)) for item in value.values()):
            entry_count = len(value)
        elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            entry_count = len(value)
        else:
            return False, None, None
        return (
            True,
            {
                "path": f"/{relative}",
                "format": "content-index-json",
                "entry_count": entry_count,
                "languages": [],
                "descriptors": [],
            },
            None,
        )
    if kind == "index":
        return (
            True,
            {
                "path": f"/{relative}",
                "format": "pagefind-binary",
                "entry_count": 1,
                "languages": [],
                "descriptors": [],
            },
            None,
        )
    return True, None, None


def _is_generated_structural_asset(relative: str) -> bool:
    lowered = relative.lower()
    name = Path(lowered).name
    return _search_asset_kind(relative) is not None or "sitemap" in name or name in {"atom.xml", "feed.xml", "rss.xml"}


HLS_ATTRIBUTE_NAME_RE = re.compile(r"[A-Z0-9-]+", re.ASCII)
HLS_TAG_NAME_RE = re.compile(r"#[A-Z0-9-]+", re.ASCII)
HLS_UNQUOTED_VALUE_RE = re.compile(
    r"(?:[A-Z0-9-]+|0[xX][0-9A-F]+|-?[0-9]+(?:\.[0-9]+)?|[0-9]+x[0-9]+)",
    re.ASCII,
)
HLS_DECIMAL_INTEGER_RE = re.compile(r"[0-9]{1,20}", re.ASCII)
HLS_DECIMAL_FLOAT_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?", re.ASCII)
HLS_SIGNED_DECIMAL_FLOAT_RE = re.compile(r"-?[0-9]+(?:\.[0-9]+)?", re.ASCII)
HLS_HEX_RE = re.compile(r"0[xX][0-9A-F]+", re.ASCII)
HLS_RESOLUTION_RE = re.compile(r"[0-9]{1,20}x[0-9]{1,20}", re.ASCII)
HLS_BYTERANGE_RE = re.compile(r"[0-9]{1,20}(?:@[0-9]{1,20})?", re.ASCII)
HLS_MAX_DECIMAL_INTEGER = (1 << 64) - 1
HLS_MAX_ATTRIBUTE_LIST_BYTES = 64 * 1024
HLS_MAX_ATTRIBUTES = 256
HLS_CREDENTIAL_QUERY_RE = CREDENTIAL_QUERY_RE


def _hls_rule(kind: str, *values: str) -> tuple[str, frozenset[str]]:
    return kind, frozenset(values)


# The parser deliberately supports a closed set of attribute-list tags.  Each
# supported attribute has its RFC/LL-HLS value type here; an unknown/new
# attribute fails closed until its semantics and URI behavior are validated.
HLS_ATTRIBUTE_SCHEMAS: dict[str, dict[str, Any]] = {
    "#EXT-X-CONTENT-STEERING": {
        "required": {"SERVER-URI"},
        "rules": {
            "SERVER-URI": _hls_rule("quoted-nonempty"),
            "PATHWAY-ID": _hls_rule("quoted-nonempty"),
        },
    },
    "#EXT-X-DATERANGE": {
        "required": {"ID", "START-DATE"},
        "allow_x_client": True,
        "rules": {
            "ID": _hls_rule("quoted-nonempty"),
            "CLASS": _hls_rule("quoted-nonempty"),
            "START-DATE": _hls_rule("quoted-nonempty"),
            "END-DATE": _hls_rule("quoted-nonempty"),
            "DURATION": _hls_rule("decimal"),
            "PLANNED-DURATION": _hls_rule("decimal"),
            "SCTE35-CMD": _hls_rule("hex"),
            "SCTE35-OUT": _hls_rule("hex"),
            "SCTE35-IN": _hls_rule("hex"),
            "END-ON-NEXT": _hls_rule("enum", "YES"),
            "CUE": _hls_rule("quoted-nonempty"),
        },
    },
    "#EXT-X-DEFINE": {
        "required": set(),
        "rules": {
            "NAME": _hls_rule("quoted-nonempty"),
            "VALUE": _hls_rule("quoted"),
            "IMPORT": _hls_rule("quoted-nonempty"),
            "QUERYPARAM": _hls_rule("quoted-nonempty"),
        },
    },
    "#EXT-X-I-FRAME-STREAM-INF": {
        "required": {"BANDWIDTH", "URI"},
        "rules": {
            "URI": _hls_rule("quoted-nonempty"),
            "BANDWIDTH": _hls_rule("integer"),
            "AVERAGE-BANDWIDTH": _hls_rule("integer"),
            "SCORE": _hls_rule("positive-decimal"),
            "CODECS": _hls_rule("quoted-nonempty"),
            "SUPPLEMENTAL-CODECS": _hls_rule("quoted-nonempty"),
            "RESOLUTION": _hls_rule("resolution"),
            "HDCP-LEVEL": _hls_rule("enum", "TYPE-0", "TYPE-1", "NONE"),
            "VIDEO-RANGE": _hls_rule("enum", "SDR", "HLG", "PQ"),
            "VIDEO": _hls_rule("quoted-nonempty"),
            "ALLOWED-CPC": _hls_rule("quoted-nonempty"),
            "REQ-VIDEO-LAYOUT": _hls_rule("quoted-nonempty"),
            "STABLE-VARIANT-ID": _hls_rule("quoted-nonempty"),
            "PATHWAY-ID": _hls_rule("quoted-nonempty"),
        },
    },
    "#EXT-X-KEY": {
        "required": {"METHOD"},
        "rules": {
            "METHOD": _hls_rule("enum", "NONE", "AES-128", "SAMPLE-AES"),
            "URI": _hls_rule("quoted-nonempty"),
            "IV": _hls_rule("iv"),
            "KEYFORMAT": _hls_rule("quoted-nonempty"),
            "KEYFORMATVERSIONS": _hls_rule("version-list"),
        },
    },
    "#EXT-X-MAP": {
        "required": {"URI"},
        "rules": {
            "URI": _hls_rule("quoted-nonempty"),
            "BYTERANGE": _hls_rule("byterange"),
        },
    },
    "#EXT-X-MEDIA": {
        "required": {"TYPE", "GROUP-ID", "NAME"},
        "rules": {
            "TYPE": _hls_rule("enum", "AUDIO", "VIDEO", "SUBTITLES", "CLOSED-CAPTIONS"),
            "URI": _hls_rule("quoted-nonempty"),
            "GROUP-ID": _hls_rule("quoted-nonempty"),
            "LANGUAGE": _hls_rule("quoted-nonempty"),
            "ASSOC-LANGUAGE": _hls_rule("quoted-nonempty"),
            "NAME": _hls_rule("quoted-nonempty"),
            "DEFAULT": _hls_rule("enum", "YES", "NO"),
            "AUTOSELECT": _hls_rule("enum", "YES", "NO"),
            "FORCED": _hls_rule("enum", "YES", "NO"),
            "INSTREAM-ID": _hls_rule("quoted-nonempty"),
            "CHARACTERISTICS": _hls_rule("quoted-nonempty"),
            "CHANNELS": _hls_rule("quoted-nonempty"),
            "STABLE-RENDITION-ID": _hls_rule("quoted-nonempty"),
            "BIT-DEPTH": _hls_rule("integer"),
            "SAMPLE-RATE": _hls_rule("integer"),
        },
    },
    "#EXT-X-PART": {
        "required": {"URI", "DURATION"},
        "rules": {
            "URI": _hls_rule("quoted-nonempty"),
            "DURATION": _hls_rule("decimal"),
            "INDEPENDENT": _hls_rule("enum", "YES"),
            "BYTERANGE": _hls_rule("byterange"),
            "GAP": _hls_rule("enum", "YES"),
        },
    },
    "#EXT-X-PART-INF": {
        "required": {"PART-TARGET"},
        "rules": {"PART-TARGET": _hls_rule("positive-decimal")},
    },
    "#EXT-X-PRELOAD-HINT": {
        "required": {"TYPE", "URI"},
        "rules": {
            "TYPE": _hls_rule("enum", "PART", "MAP"),
            "URI": _hls_rule("quoted-nonempty"),
            "BYTERANGE-START": _hls_rule("integer"),
            "BYTERANGE-LENGTH": _hls_rule("integer"),
        },
    },
    "#EXT-X-RENDITION-REPORT": {
        "required": {"URI"},
        "rules": {
            "URI": _hls_rule("quoted-nonempty"),
            "LAST-MSN": _hls_rule("integer"),
            "LAST-PART": _hls_rule("integer"),
        },
    },
    "#EXT-X-SERVER-CONTROL": {
        "required": set(),
        "rules": {
            "CAN-SKIP-UNTIL": _hls_rule("decimal"),
            "CAN-SKIP-DATERANGES": _hls_rule("enum", "YES"),
            "HOLD-BACK": _hls_rule("decimal"),
            "PART-HOLD-BACK": _hls_rule("decimal"),
            "CAN-BLOCK-RELOAD": _hls_rule("enum", "YES"),
        },
    },
    "#EXT-X-SESSION-DATA": {
        "required": {"DATA-ID"},
        "rules": {
            "DATA-ID": _hls_rule("quoted-nonempty"),
            "VALUE": _hls_rule("quoted"),
            "URI": _hls_rule("quoted-nonempty"),
            "LANGUAGE": _hls_rule("quoted-nonempty"),
            "FORMAT": _hls_rule("enum", "JSON", "RAW"),
        },
    },
    "#EXT-X-SESSION-KEY": {
        "required": {"METHOD", "URI"},
        "rules": {
            "METHOD": _hls_rule("enum", "AES-128", "SAMPLE-AES"),
            "URI": _hls_rule("quoted-nonempty"),
            "IV": _hls_rule("iv"),
            "KEYFORMAT": _hls_rule("quoted-nonempty"),
            "KEYFORMATVERSIONS": _hls_rule("version-list"),
        },
    },
    "#EXT-X-START": {
        "required": {"TIME-OFFSET"},
        "rules": {
            "TIME-OFFSET": _hls_rule("signed-decimal"),
            "PRECISE": _hls_rule("enum", "YES", "NO"),
        },
    },
    "#EXT-X-STREAM-INF": {
        "required": {"BANDWIDTH"},
        "rules": {
            "BANDWIDTH": _hls_rule("integer"),
            "AVERAGE-BANDWIDTH": _hls_rule("integer"),
            "SCORE": _hls_rule("positive-decimal"),
            "CODECS": _hls_rule("quoted-nonempty"),
            "SUPPLEMENTAL-CODECS": _hls_rule("quoted-nonempty"),
            "RESOLUTION": _hls_rule("resolution"),
            "FRAME-RATE": _hls_rule("decimal"),
            "HDCP-LEVEL": _hls_rule("enum", "TYPE-0", "TYPE-1", "NONE"),
            "VIDEO-RANGE": _hls_rule("enum", "SDR", "HLG", "PQ"),
            "AUDIO": _hls_rule("quoted-nonempty"),
            "VIDEO": _hls_rule("quoted-nonempty"),
            "SUBTITLES": _hls_rule("quoted-nonempty"),
            "CLOSED-CAPTIONS": _hls_rule("closed-captions"),
            "ALLOWED-CPC": _hls_rule("quoted-nonempty"),
            "REQ-VIDEO-LAYOUT": _hls_rule("quoted-nonempty"),
            "STABLE-VARIANT-ID": _hls_rule("quoted-nonempty"),
            "PATHWAY-ID": _hls_rule("quoted-nonempty"),
        },
    },
}


def _hls_decimal_integer(value: str) -> bool:
    return bool(HLS_DECIMAL_INTEGER_RE.fullmatch(value)) and int(value) <= HLS_MAX_DECIMAL_INTEGER


def _hls_attribute_type_valid(
    value: str,
    *,
    quoted: bool,
    rule: tuple[str, frozenset[str]],
) -> bool:
    kind, allowed = rule
    if kind == "quoted":
        return quoted
    if kind == "quoted-nonempty":
        return quoted and bool(value)
    if kind == "version-list":
        return quoted and re.fullmatch(r"[1-9][0-9]*(?:/[1-9][0-9]*)*", value, re.ASCII) is not None
    if kind == "byterange":
        if not quoted or HLS_BYTERANGE_RE.fullmatch(value) is None:
            return False
        return all(_hls_decimal_integer(item) for item in value.split("@", 1))
    if kind == "closed-captions":
        return (quoted and bool(value)) or (not quoted and value == "NONE")
    if quoted:
        return False
    if kind == "enum":
        return value in allowed
    if kind == "integer":
        return _hls_decimal_integer(value)
    if kind == "decimal":
        return HLS_DECIMAL_FLOAT_RE.fullmatch(value) is not None
    if kind == "positive-decimal":
        return HLS_DECIMAL_FLOAT_RE.fullmatch(value) is not None and float(value) > 0
    if kind == "signed-decimal":
        return HLS_SIGNED_DECIMAL_FLOAT_RE.fullmatch(value) is not None
    if kind == "hex":
        return HLS_HEX_RE.fullmatch(value) is not None
    if kind == "iv":
        return re.fullmatch(r"0[xX][0-9A-F]{1,32}", value, re.ASCII) is not None
    if kind == "resolution":
        if HLS_RESOLUTION_RE.fullmatch(value) is None:
            return False
        return all(_hls_decimal_integer(item) for item in value.split("x", 1))
    return False


def _hls_x_client_uri_status(value: str) -> tuple[bool, bool]:
    """Return URI-looking and protected-credential status for an X-* value."""

    credentialed = False
    try:
        parts = urlsplit(value)
    except ValueError:
        return True, True
    if parts.username is not None or parts.password is not None:
        credentialed = True
    try:
        if any(
            HLS_CREDENTIAL_QUERY_RE.search(key) for key, _query_value in parse_qsl(parts.query, keep_blank_values=True)
        ):
            credentialed = True
    except ValueError:
        return True, credentialed
    for component in re.split(r"[?&]", value):
        key, separator, _component_value = component.partition("=")
        if separator and HLS_CREDENTIAL_QUERY_RE.search(key):
            credentialed = True
    decoded_path = unquote(parts.path)
    uri_looking = bool(
        credentialed
        or parts.scheme
        or parts.netloc
        or value.startswith(("//", "/", "./", "../", "?", "#"))
        or parts.query
        or parts.fragment
        or "/" in decoded_path
        or "\\" in decoded_path
        or re.search(r"\.[A-Za-z][A-Za-z0-9]{0,15}$", decoded_path, re.ASCII)
    )
    return uri_looking, credentialed


def _hls_attribute_semantic_error(
    tag: str,
    attributes: dict[str, str],
    quoted: dict[str, bool],
) -> str | None:
    schema = HLS_ATTRIBUTE_SCHEMAS.get(tag)
    if schema is None:
        return "unsupported HLS attribute-list tag"
    rules: dict[str, tuple[str, frozenset[str]]] = schema["rules"]
    unknown = {
        name
        for name in attributes
        if name not in rules and not (schema.get("allow_x_client") and name.startswith("X-") and len(name) > 2)
    }
    if unknown or not set(schema["required"]).issubset(attributes):
        return f"{tag} has unsupported, missing, or invalid attributes"
    for name, value in attributes.items():
        if name in rules:
            if not _hls_attribute_type_valid(value, quoted=quoted[name], rule=rules[name]):
                return f"{tag} has unsupported, missing, or invalid attributes"
        elif not (
            quoted[name] or HLS_HEX_RE.fullmatch(value) is not None or HLS_DECIMAL_FLOAT_RE.fullmatch(value) is not None
        ):
            return f"{tag} has unsupported, missing, or invalid attributes"

    if tag == "#EXT-X-KEY":
        method = attributes["METHOD"]
        if method == "NONE" and set(attributes) != {"METHOD"}:
            return "#EXT-X-KEY METHOD=NONE forbids every other attribute"
        if method != "NONE" and "URI" not in attributes:
            return "#EXT-X-KEY requires URI unless METHOD=NONE"
    elif tag == "#EXT-X-SESSION-DATA":
        if ("VALUE" in attributes) == ("URI" in attributes):
            return "#EXT-X-SESSION-DATA requires exactly one of VALUE or URI"
        if "FORMAT" in attributes and "URI" not in attributes:
            return "#EXT-X-SESSION-DATA FORMAT requires URI"
    elif tag == "#EXT-X-DEFINE":
        shapes = (
            {"NAME", "VALUE"},
            {"IMPORT"},
            {"QUERYPARAM"},
        )
        if set(attributes) not in shapes:
            return "#EXT-X-DEFINE has unsupported, missing, or invalid attributes"
    elif tag == "#EXT-X-DATERANGE":
        if attributes.get("END-ON-NEXT") == "YES" and (
            "CLASS" not in attributes or {"DURATION", "END-DATE"}.intersection(attributes)
        ):
            return "#EXT-X-DATERANGE END-ON-NEXT has invalid companion attributes"
    elif tag == "#EXT-X-SERVER-CONTROL":
        if "CAN-SKIP-DATERANGES" in attributes and "CAN-SKIP-UNTIL" not in attributes:
            return "#EXT-X-SERVER-CONTROL CAN-SKIP-DATERANGES requires CAN-SKIP-UNTIL"
    elif tag == "#EXT-X-MEDIA":
        media_type = attributes["TYPE"]
        if "FORCED" in attributes and media_type != "SUBTITLES":
            return "#EXT-X-MEDIA FORCED is valid only for SUBTITLES"
        if {"BIT-DEPTH", "SAMPLE-RATE", "CHANNELS"}.intersection(attributes) and media_type != "AUDIO":
            return "#EXT-X-MEDIA audio attributes require TYPE=AUDIO"
        if attributes.get("DEFAULT") == "YES" and attributes.get("AUTOSELECT", "YES") != "YES":
            return "#EXT-X-MEDIA DEFAULT=YES requires AUTOSELECT=YES when present"
        if (
            media_type == "CLOSED-CAPTIONS"
            and re.fullmatch(
                r"(?:CC[1-4]|SERVICE(?:[1-9]|[1-5][0-9]|6[0-3]))",
                attributes.get("INSTREAM-ID", ""),
                re.ASCII,
            )
            is None
        ):
            return "#EXT-X-MEDIA CLOSED-CAPTIONS requires the exact INSTREAM-ID enum"
        if (
            media_type != "CLOSED-CAPTIONS"
            and "INSTREAM-ID" in attributes
            and re.fullmatch(r"[A-Za-z0-9.]+", attributes["INSTREAM-ID"], re.ASCII) is None
        ):
            return "#EXT-X-MEDIA has an invalid bis-22 INSTREAM-ID"
    return None


def _parse_hls_attributes(
    value: str,
) -> tuple[dict[str, str] | None, dict[str, bool] | None, str | None]:
    """Parse one HLS attribute-list while retaining each value's quoted form."""

    if len(value.encode("utf-8")) > HLS_MAX_ATTRIBUTE_LIST_BYTES:
        return None, None, "HLS attribute list exceeds its bound"
    if not value:
        return {}, {}, None

    attributes: dict[str, str] = {}
    quoted_values: dict[str, bool] = {}
    cursor = 0
    while cursor < len(value):
        if len(attributes) >= HLS_MAX_ATTRIBUTES:
            return None, None, "HLS attribute count exceeds its bound"
        name_match = HLS_ATTRIBUTE_NAME_RE.match(value, cursor)
        if name_match is None or name_match.end() >= len(value) or value[name_match.end()] != "=":
            return None, None, "malformed HLS attribute list"
        name = name_match.group().upper()
        if name in attributes:
            return None, None, "HLS attribute list has duplicate names"
        cursor = name_match.end() + 1
        if cursor >= len(value):
            return None, None, "malformed HLS attribute list"

        is_quoted = value[cursor] == '"'
        if is_quoted:
            cursor += 1
            value_start = cursor
            while cursor < len(value) and value[cursor] != '"':
                codepoint = ord(value[cursor])
                if codepoint < 0x20 or 0x7F <= codepoint <= 0x9F:
                    return None, None, "malformed HLS attribute list"
                cursor += 1
            if cursor >= len(value):
                return None, None, "malformed HLS attribute list"
            attribute_value = value[value_start:cursor]
            cursor += 1
        else:
            value_start = cursor
            while cursor < len(value) and value[cursor] != ",":
                cursor += 1
            attribute_value = value[value_start:cursor]
            if (
                not attribute_value
                or attribute_value != attribute_value.strip()
                or '"' in attribute_value
                or any(character.isspace() for character in attribute_value)
                or HLS_UNQUOTED_VALUE_RE.fullmatch(attribute_value) is None
            ):
                return None, None, "malformed HLS attribute list"
        attributes[name] = attribute_value
        quoted_values[name] = is_quoted
        if "{$" in attribute_value:
            return None, None, "HLS variable substitution is unsupported"

        if cursor == len(value):
            break
        if value[cursor] != ",":
            return None, None, "malformed HLS attribute list"
        cursor += 1
        if cursor == len(value):
            return None, None, "malformed HLS attribute list"
    return attributes, quoted_values, None


def _hls_attributes(value: str) -> tuple[dict[str, str] | None, str | None]:
    """Compatibility wrapper exposing values while the parser retains forms."""
    attributes, _quoted, error = _parse_hls_attributes(value)
    return attributes, error


def _hls_dependency(
    raw_url: str,
    *,
    playlist_path: str,
    role: str,
    line: int,
    origin: str,
) -> tuple[dict[str, Any], tuple[str, str] | None, dict[str, Any] | None]:
    raw_url = raw_url.strip()
    reference_digest = canonical_digest(raw_url)
    reason = ""
    if "{$" in raw_url:
        reason = "invalid"
    try:
        raw_parts = urlsplit(raw_url)
    except ValueError:
        raw_parts = urlsplit("")
        reason = "invalid"
    decoded_path = unquote(raw_parts.path)
    if not reason and (raw_parts.username is not None or raw_parts.password is not None):
        reason = "userinfo-credential"
    elif not raw_url or not raw_parts.path:
        reason = "empty"
    elif "\\" in decoded_path or ".." in decoded_path.split("/"):
        reason = "traversal"
    elif raw_parts.scheme or raw_parts.netloc or raw_url.startswith("//"):
        reason = "nonlocal"
    elif any(HLS_CREDENTIAL_QUERY_RE.search(key) for key, _value in parse_qsl(raw_parts.query, keep_blank_values=True)):
        reason = "query-credential"
    normalized = ""
    if not reason:
        try:
            normalized = normalize_reference(raw_url, route=playlist_path, origin=origin)
        except CredentialBearingUrlError:
            reason = "userinfo-credential"
        except ValueError:
            reason = "invalid"
    normalized_parts = urlsplit(normalized)
    if not reason and (normalized_parts.scheme or normalized_parts.netloc or not normalized_parts.path.startswith("/")):
        reason = "nonlocal"
    dependency = {
        "line": line,
        "reason": reason,
        "reference_digest": reference_digest,
        "role": role,
        "url": "" if reason else normalized,
    }
    if reason:
        return (
            dependency,
            None,
            {
                "code": "hls-unsafe-url",
                "path": playlist_path,
                "line": line,
                "role": role,
                "reason": reason,
                "reference_digest": dependency["reference_digest"],
            },
        )
    return dependency, (urlsplit(normalized).path, f"hls-{role}"), None


def _inspect_hls_playlist(
    root: Path,
    path: Path,
    relative: str,
    *,
    origin: str,
    limits: dict[str, int],
    verifier: StageBoundaryVerifier,
) -> tuple[
    dict[str, Any],
    list[tuple[str, str]],
    list[dict[str, Any]],
    tuple[int, str, tuple[int, ...]] | None,
]:
    playlist_path = f"/{relative}"
    findings: list[dict[str, Any]] = []
    references: list[tuple[str, str]] = []
    dependencies: list[dict[str, Any]] = []
    dateranges: list[dict[str, Any]] = []
    asset_capture: tuple[int, str, tuple[int, ...]] | None = None
    try:
        raw_playlist, identity = verifier.read_bytes_with_identity(path, limits["hls_bytes"])
        asset_capture = (len(raw_playlist), hashlib.sha256(raw_playlist).hexdigest(), identity)
        text = raw_playlist.decode("utf-8", errors="strict")
    except (MemoryError, OverflowError) as exc:
        return (
            {"kind": "invalid", "dependencies": [], "dateranges": []},
            [],
            [
                {
                    "code": "limit-exceeded",
                    "detail": str(exc) or "bounded HLS parse exhausted memory",
                    "path": playlist_path,
                    "resource": "hls",
                }
            ],
            asset_capture,
        )
    except SafeFileError as exc:
        if exc.code == "limit-exceeded":
            return (
                {"kind": "invalid", "dependencies": [], "dateranges": []},
                [],
                [{"code": "limit-exceeded", "detail": str(exc), "path": playlist_path, "resource": "hls"}],
                asset_capture,
            )
        return (
            {"kind": "invalid", "dependencies": [], "dateranges": []},
            [],
            [{"code": "invalid-hls-playlist", "path": playlist_path, "line": 0, "detail": str(exc)}],
            asset_capture,
        )
    except (OSError, UnicodeDecodeError) as exc:
        return (
            {"kind": "invalid", "dependencies": [], "dateranges": []},
            [],
            [{"code": "invalid-hls-playlist", "path": playlist_path, "line": 0, "detail": str(exc)}],
            asset_capture,
        )
    if not unicodedata.is_normalized("NFC", text):
        findings.append(
            {
                "code": "invalid-hls-playlist",
                "path": playlist_path,
                "line": 0,
                "detail": "HLS playlist is not Unicode NFC normalized",
            }
        )
    lines: list[tuple[int, str]] = []
    raw_lines = text.split("\n")
    for line_number, raw_line in enumerate(raw_lines, 1):
        has_line_feed = line_number < len(raw_lines)
        line = raw_line[:-1] if has_line_feed and raw_line.endswith("\r") else raw_line
        if not line:
            continue
        if line[0].isspace() or line[-1].isspace():
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": "HLS line contains invalid boundary whitespace or control characters",
                }
            )
            continue
        tag_name = line.partition(":")[0]
        if tag_name == "#EXT-X-SKIP":
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": "#EXT-X-SKIP is unsupported by the closed HLS graph policy",
                }
            )
            continue
        if any(ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F for character in line):
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": "HLS line contains invalid boundary whitespace or control characters",
                }
            )
            continue
        if line[:4].lower() == "#ext" and HLS_TAG_NAME_RE.fullmatch(tag_name) is None:
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": "malformed HLS tag name",
                }
            )
            continue
        lines.append((line_number, line))
    if not lines or lines[0][1] != "#EXTM3U":
        findings.append(
            {
                "code": "invalid-hls-playlist",
                "path": playlist_path,
                "line": lines[0][0] if lines else 0,
                "detail": "first nonempty line must be #EXTM3U",
            }
        )

    master = False
    pending_variant_line: int | None = None
    media_groups: dict[str, set[str]] = {
        media_type: set() for media_type in ("AUDIO", "VIDEO", "SUBTITLES", "CLOSED-CAPTIONS")
    }
    stream_group_references: list[tuple[int, dict[str, str]]] = []

    def parse_attributes(tag: str, raw_attributes: str, line_number: int) -> dict[str, str] | None:
        attributes, quoted, error = _parse_hls_attributes(raw_attributes)
        if error is not None:
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": error,
                }
            )
            return None
        assert attributes is not None and quoted is not None
        semantic_error = _hls_attribute_semantic_error(tag, attributes, quoted)
        if semantic_error is not None:
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": semantic_error,
                }
            )
        if tag == "#EXT-X-DATERANGE":
            attribute_records: list[dict[str, Any]] = []
            unsafe_extension = False
            for name, value in attributes.items():
                uri_looking, credentialed = _hls_x_client_uri_status(value) if name.startswith("X-") else (False, False)
                unsafe_extension = unsafe_extension or uri_looking
                attribute_records.append(
                    {
                        "name": name,
                        "quoted": quoted[name],
                        "value": "" if credentialed else value,
                        "value_digest": canonical_digest(value) if credentialed else "",
                    }
                )
            dateranges.append({"line": line_number, "attributes": attribute_records})
            if unsafe_extension:
                findings.append(
                    {
                        "code": "invalid-hls-playlist",
                        "path": playlist_path,
                        "line": line_number,
                        "detail": "#EXT-X-DATERANGE client attribute has an unsupported URI-looking value",
                    }
                )
        return attributes

    def report_missing_variant_uri(line_number: int) -> None:
        findings.append(
            {
                "code": "invalid-hls-playlist",
                "path": playlist_path,
                "line": line_number,
                "detail": "#EXT-X-STREAM-INF has no following variant URI",
            }
        )

    def add_dependency(raw_url: str, role: str, line_number: int) -> None:
        if len(dependencies) >= limits["hls_dependencies"]:
            if not any(finding.get("code") == "limit-exceeded" for finding in findings):
                findings.append(
                    {
                        "code": "limit-exceeded",
                        "detail": f"dependency limit {limits['hls_dependencies']} exceeded",
                        "path": playlist_path,
                        "resource": "hls",
                    }
                )
            return
        dependency, reference, finding = _hls_dependency(
            raw_url,
            playlist_path=playlist_path,
            role=role,
            line=line_number,
            origin=origin,
        )
        dependencies.append(dependency)
        if reference:
            references.append(reference)
        if finding:
            findings.append(finding)

    for line_number, line in lines[1:]:
        if not line.startswith("#"):
            suffix = Path(urlsplit(line).path).suffix.lower()
            role = (
                "variant"
                if pending_variant_line is not None
                else "subtitle"
                if suffix in {".srt", ".vtt"}
                else "segment"
            )
            add_dependency(line, role, line_number)
            pending_variant_line = None
            continue
        if line == "#EXT-X-STREAM-INF":
            master = True
            if pending_variant_line is not None:
                report_missing_variant_uri(pending_variant_line)
            pending_variant_line = line_number
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": "#EXT-X-STREAM-INF requires an attribute list",
                }
            )
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            master = True
            if pending_variant_line is not None:
                report_missing_variant_uri(pending_variant_line)
            pending_variant_line = line_number
            raw_attributes = line.partition(":")[2]
            if not raw_attributes:
                findings.append(
                    {
                        "code": "invalid-hls-playlist",
                        "path": playlist_path,
                        "line": line_number,
                        "detail": "#EXT-X-STREAM-INF requires an attribute list",
                    }
                )
                continue
            attributes = parse_attributes("#EXT-X-STREAM-INF", raw_attributes, line_number)
            if attributes is not None:
                stream_group_references.append((line_number, attributes))
            continue
        tag, separator, raw_attributes = line.partition(":")
        if tag not in HLS_ATTRIBUTE_LIST_TAGS:
            continue
        if not separator or not raw_attributes:
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": "HLS attribute-list tag requires attributes",
                }
            )
            continue
        attributes = parse_attributes(tag, raw_attributes, line_number)
        if attributes is None:
            continue
        role = ""
        if tag == "#EXT-X-DEFINE":
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": "#EXT-X-DEFINE is unsupported because variable substitution is disabled",
                }
            )
            continue
        if tag == "#EXT-X-SESSION-DATA" and "URI" in attributes:
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": "URI-bearing #EXT-X-SESSION-DATA is unsupported",
                }
            )
            continue
        if tag == "#EXT-X-CONTENT-STEERING":
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": "#EXT-X-CONTENT-STEERING is unsupported by the local-only HLS policy",
                }
            )
            continue
        if tag == "#EXT-X-MEDIA":
            master = True
            media_type = attributes.get("TYPE", "")
            group_id = attributes.get("GROUP-ID", "")
            if media_type not in media_groups:
                findings.append(
                    {
                        "code": "invalid-hls-playlist",
                        "path": playlist_path,
                        "line": line_number,
                        "detail": "#EXT-X-MEDIA has an unsupported TYPE",
                    }
                )
                continue
            if not group_id or group_id == "NONE":
                findings.append(
                    {
                        "code": "invalid-hls-playlist",
                        "path": playlist_path,
                        "line": line_number,
                        "detail": "#EXT-X-MEDIA requires a non-NONE GROUP-ID",
                    }
                )
            else:
                media_groups[media_type].add(group_id)
            if not attributes.get("NAME", ""):
                findings.append(
                    {
                        "code": "invalid-hls-playlist",
                        "path": playlist_path,
                        "line": line_number,
                        "detail": "#EXT-X-MEDIA requires NAME",
                    }
                )
            if media_type == "CLOSED-CAPTIONS":
                if "URI" in attributes:
                    findings.append(
                        {
                            "code": "invalid-hls-playlist",
                            "path": playlist_path,
                            "line": line_number,
                            "detail": "CLOSED-CAPTIONS forbids URI",
                        }
                    )
                if not re.fullmatch(
                    r"(?:CC[1-4]|SERVICE(?:[1-9]|[1-5][0-9]|6[0-3]))", attributes.get("INSTREAM-ID", "")
                ):
                    findings.append(
                        {
                            "code": "invalid-hls-playlist",
                            "path": playlist_path,
                            "line": line_number,
                            "detail": "CLOSED-CAPTIONS requires a valid INSTREAM-ID",
                        }
                    )
                continue
            uri = attributes.get("URI", "").strip()
            if media_type == "SUBTITLES":
                if uri:
                    role = "subtitle-playlist"
                else:
                    findings.append(
                        {
                            "code": "invalid-hls-playlist",
                            "path": playlist_path,
                            "line": line_number,
                            "detail": "#EXT-X-MEDIA requires URI",
                        }
                    )
            elif "URI" in attributes:
                if uri:
                    role = "rendition"
                else:
                    findings.append(
                        {
                            "code": "invalid-hls-playlist",
                            "path": playlist_path,
                            "line": line_number,
                            "detail": f"{media_type} URI must be nonempty when present",
                        }
                    )
        elif tag in {"#EXT-X-I-FRAME-STREAM-INF", "#EXT-X-RENDITION-REPORT"}:
            master = True
            role = "variant"
        elif tag in {"#EXT-X-KEY", "#EXT-X-SESSION-KEY"}:
            if tag == "#EXT-X-SESSION-KEY":
                master = True
            role = "key"
            if attributes.get("METHOD", "") == "NONE" and "URI" not in attributes:
                continue
        elif tag == "#EXT-X-MAP":
            role = "map"
        elif tag == "#EXT-X-PART":
            role = "segment"
        elif tag == "#EXT-X-PRELOAD-HINT":
            role = "map" if attributes.get("TYPE", "") == "MAP" else "segment"
        if role:
            raw_url = attributes.get("URI", "")
            if raw_url:
                add_dependency(raw_url, role, line_number)
            else:
                findings.append(
                    {
                        "code": "invalid-hls-playlist",
                        "path": playlist_path,
                        "line": line_number,
                        "detail": f"{tag} requires URI",
                    }
                )
    closed_captions_none = any(
        attributes.get("CLOSED-CAPTIONS") == "NONE" for _line, attributes in stream_group_references
    )
    closed_caption_groups = media_groups["CLOSED-CAPTIONS"]
    for line_number, attributes in stream_group_references:
        closed_captions_value = attributes.get("CLOSED-CAPTIONS")
        if (
            closed_caption_groups
            and closed_captions_value != "NONE"
            and closed_captions_value not in closed_caption_groups
        ):
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": "every variant requires a valid CLOSED-CAPTIONS group",
                }
            )
        for media_type in media_groups:
            if media_type not in attributes:
                continue
            group_id = attributes[media_type]
            if not group_id.strip():
                findings.append(
                    {
                        "code": "invalid-hls-playlist",
                        "path": playlist_path,
                        "line": line_number,
                        "detail": f"#EXT-X-STREAM-INF {media_type} group must be nonempty",
                    }
                )
                continue
            if media_type == "CLOSED-CAPTIONS" and group_id == "NONE":
                continue
            if group_id == "NONE":
                findings.append(
                    {
                        "code": "invalid-hls-playlist",
                        "path": playlist_path,
                        "line": line_number,
                        "detail": "NONE is only valid for CLOSED-CAPTIONS",
                    }
                )
                continue
            if group_id not in media_groups[media_type]:
                findings.append(
                    {
                        "code": "invalid-hls-playlist",
                        "path": playlist_path,
                        "line": line_number,
                        "detail": f"#EXT-X-STREAM-INF references an undefined {media_type} group",
                    }
                )
        if closed_captions_none and attributes.get("CLOSED-CAPTIONS") != "NONE":
            findings.append(
                {
                    "code": "invalid-hls-playlist",
                    "path": playlist_path,
                    "line": line_number,
                    "detail": "CLOSED-CAPTIONS=NONE must be consistent across variants",
                }
            )
    if pending_variant_line is not None:
        report_missing_variant_uri(pending_variant_line)
    return (
        {
            "kind": "master" if master else "media",
            "dependencies": dependencies,
            "dateranges": dateranges,
        },
        references,
        findings,
        asset_capture,
    )


def _hls_graph_findings(
    playlists: dict[str, dict[str, Any]],
    roots: list[str],
    media_paths: set[str],
    *,
    limits: dict[str, int],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    adjacency_sets: dict[str, set[str]] = {path: set() for path in playlists}
    for playlist_path, playlist in playlists.items():
        for dependency in playlist["dependencies"]:
            if dependency["reason"]:
                continue
            target_path = urlsplit(dependency["url"]).path
            if Path(target_path).suffix.lower() in HLS_PLAYLIST_SUFFIXES:
                adjacency_sets[playlist_path].add(target_path)
    adjacency = {path: sorted(targets) for path, targets in adjacency_sets.items()}

    reachable: set[str] = set()
    pending = deque((root, 0) for root in roots)
    reported_depth: set[str] = set()
    while pending:
        current, depth = pending.popleft()
        if depth > limits["hls_depth"]:
            if current not in reported_depth:
                findings.append(
                    {
                        "code": "limit-exceeded",
                        "detail": f"graph depth limit {limits['hls_depth']} exceeded",
                        "path": current,
                        "resource": "hls",
                    }
                )
                reported_depth.add(current)
            continue
        if current in reachable or current not in playlists:
            continue
        reachable.add(current)
        pending.extend((target, depth + 1) for target in adjacency[current])

    state: dict[str, int] = {}
    cycles = _BoundedCycleEvidence(
        maximum_cycles=min(limits["hls_playlists"], limits["manifest_nodes"]),
        maximum_nodes=limits["manifest_nodes"],
        maximum_bytes=limits["manifest_bytes"],
    )
    for playlist_path in sorted(playlists):
        if state.get(playlist_path, 0) != 0:
            continue
        state[playlist_path] = 1
        path_stack = [playlist_path]
        path_positions = {playlist_path: 0}
        frames = [(playlist_path, iter(adjacency[playlist_path]))]
        while frames:
            current, targets = frames[-1]
            try:
                target = next(targets)
            except StopIteration:
                state[current] = 2
                frames.pop()
                path_positions.pop(path_stack.pop(), None)
                continue
            if target not in playlists:
                continue
            if state.get(target, 0) == 0:
                state[target] = 1
                path_positions[target] = len(path_stack)
                path_stack.append(target)
                frames.append((target, iter(adjacency[target])))
            elif state.get(target) == 1:
                cycles.add_from_stack(path_stack, path_positions[target])
    findings.extend({"code": "hls-cycle", "paths": list(cycle)} for cycle in sorted(cycles.cycles))
    if cycles.exhausted:
        findings.append(
            {
                "code": "limit-exceeded",
                "detail": "HLS cycle evidence limit exceeded",
                "path": "/",
                "resource": "hls",
            }
        )

    for playlist_path in sorted(set(playlists) - reachable):
        findings.append({"code": "hls-orphan", "kind": "playlist", "path": playlist_path})
    referenced_media = {
        urlsplit(dependency["url"]).path
        for playlist_path in reachable
        for dependency in playlists[playlist_path]["dependencies"]
        if not dependency["reason"]
        and Path(urlsplit(dependency["url"]).path).suffix.lower() not in HLS_PLAYLIST_SUFFIXES
    }
    for media_path in sorted(media_paths - referenced_media):
        findings.append({"code": "hls-orphan", "kind": "media", "path": media_path})
    return sorted(findings, key=canonical_json)


def _hls_subtitle_playlist_paths(playlists: dict[str, dict[str, Any]]) -> set[str]:
    referenced = {
        urlsplit(dependency["url"]).path
        for playlist in playlists.values()
        for dependency in playlist["dependencies"]
        if not dependency["reason"] and dependency["role"] == "subtitle-playlist"
    }
    inferred = {
        playlist_path
        for playlist_path, playlist in playlists.items()
        if any(
            not dependency["reason"]
            and dependency["role"] == "subtitle"
            and Path(urlsplit(dependency["url"]).path).suffix.lower() == ".vtt"
            for dependency in playlist["dependencies"]
        )
    }
    return referenced | inferred


def _hls_target_type_findings(playlists: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    subtitle_playlists = _hls_subtitle_playlist_paths(playlists)
    for playlist_path, playlist in playlists.items():
        for dependency in playlist["dependencies"]:
            if dependency["reason"]:
                continue
            target_path = urlsplit(dependency["url"]).path
            suffix = Path(target_path).suffix.lower()
            role = dependency["role"]
            reason = ""
            webvtt_map = role == "map" and suffix == ".vtt" and playlist_path in subtitle_playlists
            if suffix not in HLS_TARGET_SUFFIXES[role] and not webvtt_map:
                reason = "extension-role-mismatch"
            elif suffix in HLS_PLAYLIST_SUFFIXES and (
                target_path not in playlists or playlists[target_path]["kind"] not in {"master", "media"}
            ):
                reason = "target-not-parsed-playlist"
            elif role == "subtitle-playlist" and playlists[target_path]["kind"] != "media":
                reason = "media-playlist-role-required"
            elif role in {"rendition", "subtitle-playlist", "variant"} and suffix not in HLS_PLAYLIST_SUFFIXES:
                reason = "playlist-role-required"
            if reason:
                findings.append(
                    {
                        "code": "hls-target-type-mismatch",
                        "line": dependency["line"],
                        "path": playlist_path,
                        "reason": reason,
                        "role": role,
                        "target_path": target_path,
                    }
                )
    for subtitle_playlist_path in sorted(subtitle_playlists):
        subtitle_playlist = playlists.get(subtitle_playlist_path)
        if not subtitle_playlist or subtitle_playlist["kind"] != "media":
            continue
        has_webvtt_content_segment = False
        for dependency in subtitle_playlist["dependencies"]:
            if dependency["reason"] or dependency["role"] not in {"map", "segment", "subtitle"}:
                continue
            target_path = urlsplit(dependency["url"]).path
            suffix = Path(target_path).suffix.lower()
            if suffix == ".vtt":
                if dependency["role"] in {"segment", "subtitle"}:
                    has_webvtt_content_segment = True
                continue
            findings.append(
                {
                    "code": "hls-target-type-mismatch",
                    "line": dependency["line"],
                    "path": subtitle_playlist_path,
                    "reason": "subtitle-segment-type-mismatch",
                    "role": dependency["role"],
                    "target_path": target_path,
                }
            )
        if not has_webvtt_content_segment:
            findings.append(
                {
                    "code": "hls-target-type-mismatch",
                    "line": 1,
                    "path": subtitle_playlist_path,
                    "reason": "subtitle-content-missing",
                    "role": "subtitle-playlist",
                    "target_path": subtitle_playlist_path,
                }
            )
    return sorted(findings, key=canonical_json)


def _is_iso_bmff(prefix: bytes) -> bool:
    if len(prefix) < 8:
        return False
    offset = 0
    allowed_first_boxes = {b"ftyp", b"moof", b"moov", b"sidx", b"styp"}
    while offset + 8 <= len(prefix):
        size = struct.unpack(">I", prefix[offset : offset + 4])[0]
        box_type = prefix[offset + 4 : offset + 8]
        header_size = 8
        if size == 1:
            if offset + 16 > len(prefix):
                return False
            size = struct.unpack(">Q", prefix[offset + 8 : offset + 16])[0]
            header_size = 16
        elif size == 0:
            size = len(prefix) - offset
        if box_type not in allowed_first_boxes or size < header_size:
            return False
        return True
    return False


def _webvtt_payload_status(value: bytes, size: int) -> tuple[bool, bool]:
    """Return (valid bounded UTF-8 WebVTT payload, contains a WEBVTT header)."""
    if len(value) != size:
        return False, False
    has_bom = value.startswith(b"\xef\xbb\xbf")
    payload = value[3:] if has_bom else value
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False, False
    if any(
        (ord(character) < 0x20 and character not in {"\t", "\r", "\n"}) or 0x7F <= ord(character) <= 0x9F
        for character in text
    ):
        return False, False
    has_header = text.startswith("WEBVTT") and text[6:7] in {"", "\r", "\n", " ", "\t"}
    if has_bom and not has_header:
        return False, False
    return bool(text), has_header


def _hls_detected_media_type(
    prefix: bytes,
    size: int,
    suffix: str,
    *,
    allow_headerless_webvtt: bool = False,
) -> str:
    if suffix == ".key":
        return "application/octet-stream" if size == 16 else ""
    if suffix in {".m4s", ".mp4"}:
        return "video/mp4" if _is_iso_bmff(prefix) else ""
    if suffix == ".ts":
        if size < 188 or len(prefix) < 188:
            return ""
        packet_count = min(len(prefix) // 188, 3)
        return (
            "video/mp2t" if packet_count and all(prefix[index * 188] == 0x47 for index in range(packet_count)) else ""
        )
    if suffix == ".aac":
        return "audio/aac" if len(prefix) >= 2 and prefix[0] == 0xFF and prefix[1] & 0xF6 == 0xF0 else ""
    if suffix == ".mp3":
        return (
            "audio/mpeg"
            if prefix.startswith(b"ID3") or (len(prefix) >= 2 and prefix[0] == 0xFF and prefix[1] & 0xE0 == 0xE0)
            else ""
        )
    if suffix == ".vtt":
        valid, has_header = _webvtt_payload_status(prefix, size)
        return "text/vtt" if valid and (has_header or allow_headerless_webvtt) else ""
    if suffix == ".srt":
        try:
            text = prefix.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return ""
        return "application/x-subrip" if re.search(r"\d{2}:\d{2}:\d{2},\d{3}\s+-->\s+", text) else ""
    return ""


def _hls_expected_media_type(role: str, suffix: str, playlist_path: str, subtitle_playlists: set[str]) -> str | None:
    if role == "map" and suffix == ".vtt" and playlist_path in subtitle_playlists:
        return "text/vtt"
    return HLS_ROLE_MIME.get((role, suffix))


def _hls_payload_findings_from_records(
    playlists: dict[str, dict[str, Any]],
    assets: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    checked: set[tuple[str, str, bool]] = set()
    subtitle_playlists = _hls_subtitle_playlist_paths(playlists)
    for playlist_path, playlist in playlists.items():
        active_webvtt_map = False
        for dependency in playlist["dependencies"]:
            if dependency["reason"]:
                continue
            target_path = urlsplit(dependency["url"]).path
            suffix = Path(target_path).suffix.lower()
            expected = _hls_expected_media_type(dependency["role"], suffix, playlist_path, subtitle_playlists)
            asset = assets.get(target_path)
            if dependency["role"] == "map":
                active_webvtt_map = bool(
                    playlist_path in subtitle_playlists
                    and suffix == ".vtt"
                    and asset
                    and asset["exists"]
                    and asset["decoded_media_type"] == "text/vtt"
                    and asset["webvtt_header"]
                )
            require_webvtt_header = bool(
                expected == "text/vtt"
                and (
                    dependency["role"] == "map"
                    or (dependency["role"] in {"segment", "subtitle"} and not active_webvtt_map)
                )
            )
            identity = (target_path, dependency["role"], require_webvtt_header)
            if expected is None or identity in checked or not asset or not asset["exists"]:
                continue
            checked.add(identity)
            if asset["decoded_media_type"] != expected or (require_webvtt_header and not asset["webvtt_header"]):
                findings.append(
                    {
                        "code": "hls-target-type-mismatch",
                        "line": dependency["line"],
                        "path": playlist_path,
                        "reason": "payload-media-type-mismatch",
                        "role": dependency["role"],
                        "target_path": target_path,
                    }
                )
    return sorted(findings, key=canonical_json)


def _hls_payload_findings_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return _hls_payload_findings_from_records(manifest["hls"]["playlists"], manifest["assets"])


def _feature_record(
    name: str,
    markup: set[str],
    runtime_assets: set[str],
    index_assets: set[str],
    index_documents: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    routes: dict[str, set[str]],
    suppressed_routes: dict[str, set[str]],
) -> dict[str, Any]:
    controls = sorted(markup) if name in {"breadcrumbs", "dark", "reader", "search"} else []
    rendered_markup = [] if controls else sorted(markup)
    if name == "search":
        pagefind_index = any("pagefind" in path.lower() for path in index_assets)
        pagefind_entry = any(document.get("format") == "pagefind-entry" for document in index_documents)
        matching_runtime = bool(runtime_assets) and (
            not pagefind_index
            or any(
                "pagefind" in path.lower() and Path(path).suffix.lower() in {".js", ".mjs"} for path in runtime_assets
            )
        )
        present = bool(controls and matching_runtime and index_assets and (not pagefind_index or pagefind_entry))
    elif name in {"robots", "rss", "sitemap"}:
        present = bool(documents)
    else:
        present = bool(controls or rendered_markup)
    return {
        "present": present,
        "verification": "structural-only",
        "evidence": {
            "controls": controls,
            "documents": sorted(documents, key=canonical_json),
            "index_assets": sorted(index_assets),
            "index_documents": sorted(index_documents, key=canonical_json),
            "markup": rendered_markup,
            "runtime_assets": sorted(runtime_assets),
            "routes": {route: sorted(items) for route, items in sorted(routes.items())},
            "suppressed_routes": {route: sorted(items) for route, items in sorted(suppressed_routes.items())},
        },
    }


def _build_manifest_under_lease(
    root: Path,
    *,
    source_snapshot: dict[str, Any],
    origin: str = DEFAULT_ORIGIN,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Create a deterministic parity manifest for one static build directory."""
    origin = normalize_origin(origin)
    limits = _resolved_limits(limits)
    root, tree_files, unsafe_findings, tree_snapshot = _tree_preflight(root, limits)
    if unsafe_findings:
        return _empty_manifest(origin, unsafe_findings, limits, source_snapshot)
    if tree_snapshot is None:
        return _empty_manifest(
            origin,
            [
                {
                    "code": "unsafe-tree-read-error",
                    "detail": "release tree preflight did not produce a complete inventory",
                    "path": "/",
                }
            ],
            limits,
            source_snapshot,
        )
    verifier = StageBoundaryVerifier(root, tree_snapshot, limits)
    available_tree_paths = {f"/{path.relative_to(root).as_posix()}": path for path in tree_files}
    routes: dict[str, dict[str, Any]] = {}
    aliases: dict[str, dict[str, Any]] = {}
    features: dict[str, set[str]] = {name: set() for name in FEATURE_NAMES}
    feature_runtime_assets: dict[str, set[str]] = {name: set() for name in FEATURE_NAMES}
    feature_index_assets: dict[str, set[str]] = {name: set() for name in FEATURE_NAMES}
    feature_index_documents: dict[str, list[dict[str, Any]]] = {name: [] for name in FEATURE_NAMES}
    feature_documents: dict[str, list[dict[str, Any]]] = {name: [] for name in FEATURE_NAMES}
    feature_routes: dict[str, dict[str, set[str]]] = {name: {} for name in FEATURE_NAMES}
    suppressed_feature_routes: dict[str, dict[str, set[str]]] = {name: {} for name in FEATURE_NAMES}
    hls_playlists: dict[str, dict[str, Any]] = {}
    hls_playlist_captures: dict[str, tuple[int, str, tuple[int, ...]]] = {}
    asset_references: dict[str, set[tuple[str, str]]] = {}
    duplicate_sources: dict[tuple[str, str], set[str]] = {}
    integrity_findings: list[dict[str, Any]] = []
    asset_reference_limit_hit = False

    def add_asset_reference(web_path: str, route: str, kind: str) -> None:
        nonlocal asset_reference_limit_hit
        if len(web_path.encode("utf-8")) > limits["path_bytes"]:
            if not asset_reference_limit_hit:
                integrity_findings.append(
                    {
                        "code": "limit-exceeded",
                        "detail": f"asset path byte limit {limits['path_bytes']} exceeded",
                        "path": "/",
                        "resource": "asset",
                    }
                )
                asset_reference_limit_hit = True
            return
        if web_path not in asset_references and len(asset_references) >= limits["asset_count"]:
            if not asset_reference_limit_hit:
                integrity_findings.append(
                    {
                        "code": "limit-exceeded",
                        "detail": f"asset count limit {limits['asset_count']} exceeded",
                        "path": "/",
                        "resource": "asset",
                    }
                )
                asset_reference_limit_hit = True
            return
        asset_references.setdefault(web_path, set()).add((route, kind))

    html_paths = [path for path in tree_files if path.suffix.lower() == ".html"]
    if len(html_paths) > limits["html_files"]:
        integrity_findings.append(
            {
                "code": "limit-exceeded",
                "detail": f"file count limit {limits['html_files']} exceeded",
                "path": "/",
                "resource": "html",
            }
        )
        html_paths = html_paths[: limits["html_files"]]
    for html_path in html_paths:
        relative = html_path.relative_to(root)
        page_physical_route = physical_route(relative)
        try:
            html_text = verifier.read_bytes(html_path, limits["html_bytes"]).decode("utf-8", errors="strict")
            base_parser = BaseHrefParser()
            base_parser.feed(html_text)
            base_parser.close()
            parser = StaticPageParser(
                physical_route_value=page_physical_route,
                origin=origin,
                base_href_raw=base_parser.href,
                base_target_raw=base_parser.target,
                limits=limits,
            )
            parser.feed(html_text)
            parser.close()
            parser.resolve_form_controls()
        except (MemoryError, OverflowError, SafeFileError) as exc:
            if isinstance(exc, SafeFileError) and exc.code != "limit-exceeded":
                integrity_findings.append(
                    {
                        "code": "invalid-html",
                        "detail": str(exc),
                        "path": f"/{relative.as_posix()}",
                    }
                )
                continue
            integrity_findings.append(
                {
                    "code": "limit-exceeded",
                    "detail": str(exc) or "bounded HTML parse exhausted memory",
                    "path": f"/{relative.as_posix()}",
                    "resource": "html",
                }
            )
            continue
        except (OSError, RecursionError, UnicodeDecodeError, ValueError) as exc:
            integrity_findings.append(
                {
                    "code": "invalid-html",
                    "detail": str(exc),
                    "path": f"/{relative.as_posix()}",
                }
            )
            continue

        refresh_delay, alias_target = parser.refresh()
        if alias_target:
            if page_physical_route in aliases:
                duplicate_sources.setdefault(("duplicate-alias", page_physical_route), set()).update(
                    {aliases[page_physical_route]["source"], relative.as_posix()}
                )
            else:
                alias_metadata = parser.metadata()
                aliases[page_physical_route] = {
                    "source": relative.as_posix(),
                    "target": alias_target,
                    "refresh_delay": refresh_delay,
                    "refreshes": parser.refresh_records,
                    "canonical": alias_metadata["canonical"],
                    "robots": alias_metadata["robots"],
                    "robots_by_crawler": alias_metadata["robots_by_crawler"],
                    "noindex": alias_metadata["noindex"],
                    "follow": "nofollow" not in alias_metadata["robots"],
                }
            for finding in parser.html_findings:
                integrity_findings.append({**finding, "route": page_physical_route})
            continue

        route, page = _page_manifest(parser, source=relative.as_posix())
        if route in routes:
            duplicate_sources.setdefault(("duplicate-route", route), set()).update(
                {routes[route]["source"], relative.as_posix()}
            )
            continue
        refs = page.pop("_asset_refs")
        routes[route] = page
        for finding in parser.grafana_findings:
            integrity_findings.append({**finding, "route": route})
        for finding in parser.html_findings:
            integrity_findings.append({**finding, "route": route})
        for web_path, kind in refs:
            add_asset_reference(web_path, route, kind)
        for web_path, kind in parser.runtime_asset_refs:
            add_asset_reference(web_path, route, kind)
            if (
                kind in {"module", "script"}
                and parser.features["search"]
                and urlsplit(web_path).path in available_tree_paths
            ):
                feature_runtime_assets["search"].add(web_path)
        for name, evidence in parser.features.items():
            for item in evidence:
                features[name].add(f"{route}: {item}")
                feature_routes[name].setdefault(route, set()).add(item)
        for name, evidence in parser.suppressed_features.items():
            for item in evidence:
                suppressed_feature_routes[name].setdefault(route, set()).add(item)
        if page["downloads"]:
            evidence = f"{len(page['downloads'])} download(s)"
            features["downloads"].add(f"{route}: {evidence}")
            feature_routes["downloads"].setdefault(route, set()).add(evidence)

    for route in sorted(set(routes) & set(aliases)):
        duplicate_sources.setdefault(("duplicate-alias", route), set()).update(
            {routes[route]["source"], aliases[route]["source"]}
        )

    hls_candidates = [path for path in tree_files if path.suffix.lower() in HLS_PLAYLIST_SUFFIXES]
    hls_directories = {path.parent for path in hls_candidates}
    allowed_hls = set(hls_candidates[: limits["hls_playlists"]])
    if len(hls_candidates) > limits["hls_playlists"]:
        integrity_findings.append(
            {
                "code": "limit-exceeded",
                "detail": f"playlist count limit {limits['hls_playlists']} exceeded",
                "path": "/",
                "resource": "hls",
            }
        )
    hls_media_paths: set[str] = set()
    for path in tree_files:
        relative = path.relative_to(root).as_posix()
        lowered = relative.lower()
        suffix = path.suffix.lower()
        structural_xml = False
        if suffix in HLS_PLAYLIST_SUFFIXES:
            if path not in allowed_hls:
                continue
            playlist_path = f"/{relative}"
            playlist, hls_references, hls_findings, playlist_capture = _inspect_hls_playlist(
                root,
                path,
                relative,
                origin=origin,
                limits=limits,
                verifier=verifier,
            )
            hls_playlists[playlist_path] = playlist
            if playlist_capture is not None:
                hls_playlist_captures[playlist_path] = playlist_capture
            add_asset_reference(playlist_path, "", "hls-playlist")
            for dependency_path, dependency_kind in hls_references:
                add_asset_reference(dependency_path, "", dependency_kind)
            integrity_findings.extend(hls_findings)
        elif suffix in HLS_MEDIA_SUFFIXES and any(
            directory == path.parent or directory in path.parents for directory in hls_directories
        ):
            media_path = f"/{relative}"
            directly_referenced = any(
                route and kind in {"download", "media", "public"}
                for route, kind in asset_references.get(media_path, set())
            )
            if not directly_referenced:
                hls_media_paths.add(media_path)
                add_asset_reference(media_path, "", "hls-media")
        search_kind = _search_asset_kind(relative)
        if search_kind:
            asset_kind = "search-runtime" if search_kind == "runtime" else "search-index"
            add_asset_reference(f"/{relative}", "", asset_kind)
            valid_search_asset, search_document, search_limit_error = _inspect_search_asset(
                root,
                path,
                relative,
                search_kind,
                available_paths=available_tree_paths,
                limits=limits,
                verifier=verifier,
            )
            if valid_search_asset:
                target = feature_runtime_assets if search_kind == "runtime" else feature_index_assets
                target["search"].add(f"/{relative}")
                if search_document:
                    feature_index_documents["search"].append(search_document)
            else:
                if search_limit_error:
                    integrity_findings.append(
                        {
                            "code": "limit-exceeded",
                            "detail": search_limit_error,
                            "path": f"/{relative}",
                            "resource": "json",
                        }
                    )
                else:
                    integrity_findings.append({"code": f"invalid-search-{search_kind}", "path": f"/{relative}"})
        if "katex" in lowered or "mathjax" in lowered:
            feature_runtime_assets["katex"].add(f"/{relative}")
        if lowered.endswith(".xml"):
            feature, evidence, findings = _inspect_xml_feature(
                root,
                path,
                relative,
                origin=origin,
                limits=limits,
                verifier=verifier,
            )
            structural_xml = feature is not None
            if feature and evidence:
                feature_documents[feature].append(evidence)
            integrity_findings.extend(findings)
        if relative == "robots.txt":
            evidence, finding = _inspect_robots(
                root,
                path,
                origin=origin,
                limits=limits,
                verifier=verifier,
            )
            if evidence:
                feature_documents["robots"].append(evidence)
            if finding:
                integrity_findings.append(finding)
        if (
            path.suffix.lower() in PUBLIC_SEMANTIC_SUFFIXES
            and not structural_xml
            and not _is_generated_structural_asset(relative)
        ):
            web_path = normalize_reference(f"/{relative}", route="/", origin=origin)
            add_asset_reference(urlsplit(web_path).path, "", "public")

    integrity_findings.extend(_alias_integrity_findings(routes, aliases))
    canonical_indexable_routes = _canonical_indexable_routes(routes, aliases)
    integrity_findings.extend(_rss_route_findings(feature_documents["rss"], canonical_indexable_routes))
    _reachable_sitemaps, sitemap_graph_findings = _sitemap_closure(
        feature_documents["sitemap"],
        feature_documents["robots"],
        limits=limits,
    )
    integrity_findings.extend(sitemap_graph_findings)
    integrity_findings.extend(
        _sitemap_route_findings(
            feature_documents["sitemap"],
            canonical_indexable_routes,
            feature_documents["robots"],
            limits=limits,
        )
    )
    integrity_findings.extend(_robots_sitemap_findings(feature_documents["robots"], feature_documents["sitemap"]))
    integrity_findings.extend(_runtime_integrity_findings(routes))

    hls_roots = sorted(
        path
        for path, references in asset_references.items()
        if Path(urlsplit(path).path).suffix.lower() in HLS_PLAYLIST_SUFFIXES
        and any(route for route, _kind in references)
    )
    integrity_findings.extend(_hls_graph_findings(hls_playlists, hls_roots, hls_media_paths, limits=limits))
    integrity_findings.extend(_hls_target_type_findings(hls_playlists))

    for (code, route), sources in sorted(duplicate_sources.items()):
        integrity_findings.append({"code": code, "route": route, "sources": sorted(sources)})

    assets: dict[str, dict[str, Any]] = {}
    for asset_path, references in sorted(asset_references.items()):
        if not is_local_reference(asset_path):
            continue
        try:
            playlist_capture = hls_playlist_captures.get(asset_path)
            if playlist_capture is None:
                assets[asset_path] = _asset_record(
                    root,
                    asset_path,
                    references,
                    limits=limits,
                    verifier=verifier,
                )
            else:
                size, digest, captured_identity = playlist_capture
                with _safe_open_release_file(
                    root,
                    root / unquote(asset_path).lstrip("/"),
                    limits["hls_bytes"],
                ) as (_file_descriptor, current):
                    if _file_identity(current) != captured_identity:
                        raise SafeFileError("unsafe-tree-identity-change", "release playlist changed after parsing")
                assets[asset_path] = {
                    "decoded_media_type": "",
                    "exists": True,
                    "size": size,
                    "sha256": digest,
                    "webvtt_header": False,
                    "references": [
                        {"kind": kind, "route": route}
                        for route, kind in sorted(references, key=lambda item: (item[0], item[1]))
                    ],
                }
        except SafeFileError as exc:
            assets[asset_path] = {
                "decoded_media_type": "",
                "exists": False,
                "size": None,
                "sha256": None,
                "webvtt_header": False,
                "references": [
                    {"kind": kind, "route": route}
                    for route, kind in sorted(references, key=lambda item: (item[0], item[1]))
                ],
            }
            if exc.code == "limit-exceeded":
                integrity_findings.append(
                    {
                        "code": "limit-exceeded",
                        "detail": str(exc),
                        "path": asset_path,
                        "resource": "asset",
                    }
                )
            else:
                integrity_findings.append(
                    {
                        "code": "unsafe-tree-read-error",
                        "detail": str(exc),
                        "path": asset_path,
                    }
                )
    integrity_findings.extend(_hls_payload_findings_from_records(hls_playlists, assets))
    integrity_findings.extend(_grafana_fallback_file_findings(routes, assets))
    stage_findings = verifier.verify()
    if stage_findings:
        return _empty_manifest(origin, stage_findings, limits, source_snapshot)
    missing_assets = sorted(path for path, record in assets.items() if not record["exists"])
    return {
        "contract": CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "source_snapshot": _json_deep_copy(source_snapshot),
        "origin": origin,
        "limits": limits,
        "routes": dict(sorted(routes.items())),
        "aliases": dict(sorted(aliases.items())),
        "assets": assets,
        "hls": {
            "policy": _authority_copy(_HLS_POLICY_AUTHORITY_JSON),
            "playlists": dict(sorted(hls_playlists.items())),
            "roots": hls_roots,
        },
        "features": {
            name: _feature_record(
                name,
                features[name],
                feature_runtime_assets[name],
                feature_index_assets[name],
                feature_index_documents[name],
                feature_documents[name],
                feature_routes[name],
                suppressed_feature_routes[name],
            )
            for name in FEATURE_NAMES
        },
        "integrity": {
            "missing_assets": missing_assets,
            "findings": sorted(integrity_findings, key=canonical_json),
        },
        "verification_scope": _verification_scope_for(source_snapshot),
    }


def build_manifest(
    root: Path,
    *,
    source_snapshot: dict[str, Any],
    origin: str = DEFAULT_ORIGIN,
    limits: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build one manifest while holding the cooperative snapshot lease."""

    normalized_origin = normalize_origin(origin)
    resolved_limits = _resolved_limits(limits)
    manifest: dict[str, Any] | None = None
    try:
        with StageSnapshotLease(root) as lease:
            lease.verify()
            manifest = _build_manifest_under_lease(
                root,
                source_snapshot=source_snapshot,
                origin=normalized_origin,
                limits=resolved_limits,
            )
            lease.verify()
            return manifest
    except SafeFileError as exc:
        finding = (
            {"code": "unsafe-tree-symlink", "path": "/"}
            if exc.code == "unsafe-tree-symlink"
            else {
                "code": "unsafe-tree-read-error",
                "detail": "release snapshot lease changed before final evidence",
                "path": "/",
            }
        )
        if (
            manifest is not None
            and not manifest.get("routes")
            and not manifest.get("assets")
            and manifest.get("integrity", {}).get("findings")
        ):
            manifest["integrity"]["findings"] = sorted(
                {
                    canonical_json(existing): existing for existing in [*manifest["integrity"]["findings"], finding]
                }.values(),
                key=canonical_json,
            )
            return manifest
        return _empty_manifest(normalized_origin, [finding], resolved_limits, source_snapshot)


def _missing_multiset(baseline: list[Any], candidate: list[Any]) -> list[Any]:
    baseline_by_key = {canonical_json(item): item for item in baseline}
    counts = Counter(canonical_json(item) for item in candidate)
    missing: list[Any] = []
    for item in baseline:
        key = canonical_json(item)
        if counts[key]:
            counts[key] -= 1
        else:
            missing.append(baseline_by_key[key])
    return missing


def _additional_multiset(baseline: list[Any], candidate: list[Any]) -> list[Any]:
    counts = Counter(canonical_json(item) for item in baseline)
    additional: list[Any] = []
    for item in candidate:
        key = canonical_json(item)
        if counts[key]:
            counts[key] -= 1
        else:
            additional.append(item)
    return additional


def _missing_links(baseline: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Match link identity exactly while allowing candidate link-text additions."""

    remaining = list(candidate)
    unmatched: list[dict[str, Any]] = []
    for item in baseline:
        key = canonical_json(item)
        exact = next(
            (index for index, candidate_item in enumerate(remaining) if canonical_json(candidate_item) == key), None
        )
        if exact is None:
            unmatched.append(item)
        else:
            remaining.pop(exact)

    missing: list[dict[str, Any]] = []
    for item in unmatched:
        identity = {key: value for key, value in item.items() if key != "text"}
        baseline_tokens = semantic_tokens(item.get("text", ""))
        matches: list[tuple[int, int, str]] = []
        for index, candidate_item in enumerate(remaining):
            if {key: value for key, value in candidate_item.items() if key != "text"} != identity:
                continue
            candidate_tokens = semantic_tokens(candidate_item.get("text", ""))
            missing_tokens, _additional_tokens = _subsequence_delta(baseline_tokens, candidate_tokens)
            if not missing_tokens:
                matches.append((len(candidate_tokens), index, canonical_json(candidate_item)))
        if not matches:
            missing.append(item)
            continue
        _length, index, _canonical = min(matches)
        remaining.pop(index)
    return missing


def _comparison_items(field: str, items: list[Any]) -> list[Any]:
    if field == "headings":
        return [{**item, "text": compatible_text(item.get("text", ""))} for item in items]
    if field == "tables":
        return [
            {
                **item,
                "caption": compatible_text(item.get("caption", "")),
                "rows": [
                    [{**cell, "text": compatible_text(cell.get("text", ""))} for cell in row]
                    for row in item.get("rows", [])
                ],
            }
            for item in items
        ]
    if field == "links":
        return [{**item, "text": compatible_text(item.get("text", ""))} for item in items]
    if field == "media":
        projected = []
        for item in items:
            media_source = item.get("src", "")
            media_parts = urlsplit(media_source)
            if (
                item.get("kind") == "iframe"
                and media_parts.scheme == "https"
                and media_parts.netloc == urlsplit(GRAFANA_ALLOWED_ORIGIN).netloc
                and GRAFANA_PATH_RE.match(media_parts.path)
            ):
                # Grafana presentation is compared once through its normalized
                # panel semantics below. Requiring the legacy iframe as media
                # as well would reject the supported live-link/local-fallback
                # representation despite an identical dashboard and panel.
                continue
            source_attribute = item.get("source_attribute", "")
            role = source_attribute or "src"
            value = {
                "kind": item.get("kind", ""),
                "role": role,
                "src": item.get("src", ""),
                "title": item.get("title", ""),
                "media": item.get("media", ""),
                "type": item.get("type", ""),
            }
            if role.startswith("srcset:"):
                value["sizes"] = item.get("sizes", "")
            if item.get("kind") == "img":
                value["alt"] = compatible_text(item.get("alt", ""))
            value["title"] = compatible_text(value["title"])
            projected.append(value)
        return projected
    if field == "grafana":
        return [
            {
                "panel_id": item.get("panel_id", "") or item.get("view_panel", ""),
                "time_range": item.get("time_range", {}),
                "uid": item.get("uid", ""),
                "variables": item.get("variables", {}),
            }
            for item in items
        ]
    return items


def _subsequence_delta(baseline: list[str], candidate: list[str]) -> tuple[list[str], list[str]]:
    """Return missing baseline tokens and additional candidate tokens."""
    baseline_index = 0
    additional: list[str] = []
    for token in candidate:
        if baseline_index < len(baseline) and token == baseline[baseline_index]:
            baseline_index += 1
        else:
            additional.append(token)
    return baseline[baseline_index:], additional


def _semantic_asset_paths(manifest: dict[str, Any]) -> set[str]:
    return {
        path
        for path, record in manifest.get("assets", {}).items()
        if any(
            reference.get("kind") in SEMANTIC_ASSET_KINDS | HLS_ASSET_KINDS
            for reference in record.get("references", [])
        )
    }


def _manifest_schema_error(label: str, path: str, detail: str) -> None:
    raise ValueError(f"{label} manifest {path} {detail}")


def _exact_object(value: Any, keys: set[str], *, label: str, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _manifest_schema_error(label, path, "must be a JSON object")
    actual = set(value)
    if actual != keys:
        _manifest_schema_error(
            label,
            path,
            f"keys mismatch: unknown={sorted(actual - keys)}, missing={sorted(keys - actual)}",
        )
    return value


def _string(value: Any, *, label: str, path: str, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        suffix = "nonempty string" if nonempty else "string"
        _manifest_schema_error(label, path, f"must be a {suffix}")
    return value


def _integer(value: Any, *, label: str, path: str, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        suffix = f" >= {minimum}" if minimum is not None else ""
        _manifest_schema_error(label, path, f"must be an integer{suffix}")
    return value


def _string_list(value: Any, *, label: str, path: str, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        suffix = "nonempty " if nonempty else ""
        _manifest_schema_error(label, path, f"must be a {suffix}JSON array of strings")
    for index, item in enumerate(value):
        _string(item, label=label, path=f"{path}[{index}]")
    return value


def _string_list_map(
    value: Any,
    *,
    label: str,
    path: str,
    allowed_keys: frozenset[str] | set[str] | None = None,
) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        _manifest_schema_error(label, path, "must be a JSON object")
    for key, items in value.items():
        _string(key, label=label, path=f"{path} key", nonempty=True)
        if allowed_keys is not None and key not in allowed_keys:
            _manifest_schema_error(label, path, f"contains unsupported key {key!r}")
        _string_list(items, label=label, path=f"{path}.{key}")
    return value


def _validate_metadata(metadata: Any, *, label: str, path: str) -> None:
    metadata = _exact_object(
        metadata,
        {
            "base_href",
            "canonical",
            "description",
            "lang",
            "noindex",
            "open_graph",
            "robots",
            "robots_by_crawler",
            "refreshes",
            "title",
            "twitter",
        },
        label=label,
        path=path,
    )
    for key in ("base_href", "canonical", "description", "lang", "title"):
        _string(metadata[key], label=label, path=f"{path}.{key}")
    if type(metadata["noindex"]) is not bool:
        _manifest_schema_error(label, f"{path}.noindex", "must be a JSON boolean")
    _string_list(metadata["robots"], label=label, path=f"{path}.robots")
    if metadata["noindex"] != ("noindex" in metadata["robots"]):
        _manifest_schema_error(label, f"{path}.noindex", "must be recomputed from the general robots policy")
    _string_list_map(
        metadata["robots_by_crawler"],
        label=label,
        path=f"{path}.robots_by_crawler",
        allowed_keys=ROBOT_META_NAMES,
    )
    if not isinstance(metadata["refreshes"], list):
        _manifest_schema_error(label, f"{path}.refreshes", "must be a JSON array")
    for index, refresh in enumerate(metadata["refreshes"]):
        refresh_path = f"{path}.refreshes[{index}]"
        refresh = _exact_object(refresh, {"delay", "target"}, label=label, path=refresh_path)
        _string(refresh["delay"], label=label, path=f"{refresh_path}.delay", nonempty=True)
        _string(refresh["target"], label=label, path=f"{refresh_path}.target", nonempty=True)
    open_graph = _exact_object(
        metadata["open_graph"],
        {"description", "image", "title", "type"},
        label=label,
        path=f"{path}.open_graph",
    )
    twitter = _exact_object(
        metadata["twitter"],
        {"card", "description", "image", "title"},
        label=label,
        path=f"{path}.twitter",
    )
    for group_name, group in (("open_graph", open_graph), ("twitter", twitter)):
        for key, value in group.items():
            _string(value, label=label, path=f"{path}.{group_name}.{key}")


def _validate_page_collections(page: dict[str, Any], *, label: str, route_path: str) -> None:
    headings = page["headings"]
    if not isinstance(headings, list):
        _manifest_schema_error(label, f"{route_path}.headings", "must be a JSON array")
    for index, heading in enumerate(headings):
        item_path = f"{route_path}.headings[{index}]"
        heading = _exact_object(heading, {"level", "text"}, label=label, path=item_path)
        level = _integer(heading["level"], label=label, path=f"{item_path}.level")
        if level not in range(1, 7):
            _manifest_schema_error(label, f"{item_path}.level", "must be between 1 and 6")
        _string(heading["text"], label=label, path=f"{item_path}.text")

    tables = page["tables"]
    if not isinstance(tables, list):
        _manifest_schema_error(label, f"{route_path}.tables", "must be a JSON array")
    for table_index, table in enumerate(tables):
        table_path = f"{route_path}.tables[{table_index}]"
        table = _exact_object(table, {"caption", "rows"}, label=label, path=table_path)
        _string(table["caption"], label=label, path=f"{table_path}.caption")
        if not isinstance(table["rows"], list):
            _manifest_schema_error(label, f"{table_path}.rows", "must be a JSON array")
        for row_index, row in enumerate(table["rows"]):
            row_path = f"{table_path}.rows[{row_index}]"
            if not isinstance(row, list):
                _manifest_schema_error(label, row_path, "must be a JSON array")
            for cell_index, cell in enumerate(row):
                cell_path = f"{row_path}[{cell_index}]"
                cell = _exact_object(cell, {"kind", "text"}, label=label, path=cell_path)
                if cell["kind"] not in {"td", "th"}:
                    _manifest_schema_error(label, f"{cell_path}.kind", "must be 'td' or 'th'")
                _string(cell["text"], label=label, path=f"{cell_path}.text")

    collection_schemas = {
        "links": {
            "download",
            "download_filename",
            "href",
            "hreflang",
            "referrerpolicy",
            "rel",
            "target",
            "text",
            "type",
        },
        "downloads": {"filename", "href", "text"},
    }
    for collection, keys in collection_schemas.items():
        items = page[collection]
        if not isinstance(items, list):
            _manifest_schema_error(label, f"{route_path}.{collection}", "must be a JSON array")
        for index, item in enumerate(items):
            item_path = f"{route_path}.{collection}[{index}]"
            item = _exact_object(item, keys, label=label, path=item_path)
            for key, value in item.items():
                if key == "download":
                    if type(value) is not bool:
                        _manifest_schema_error(label, f"{item_path}.download", "must be a JSON boolean")
                elif key == "rel":
                    _string_list(value, label=label, path=f"{item_path}.rel")
                else:
                    _string(value, label=label, path=f"{item_path}.{key}")

    media = page["media"]
    if not isinstance(media, list):
        _manifest_schema_error(label, f"{route_path}.media", "must be a JSON array")
    required_media_keys = {"kind", "src"}
    optional_media_keys = {"alt", "media", "sizes", "source_attribute", "title", "type"}
    for index, item in enumerate(media):
        item_path = f"{route_path}.media[{index}]"
        if not isinstance(item, dict):
            _manifest_schema_error(label, item_path, "must be a JSON object")
        unknown = set(item) - required_media_keys - optional_media_keys
        missing = required_media_keys - set(item)
        if unknown or missing:
            _manifest_schema_error(
                label,
                item_path,
                f"keys mismatch: unknown={sorted(unknown)}, missing={sorted(missing)}",
            )
        for key, value in item.items():
            _string(value, label=label, path=f"{item_path}.{key}")

    revealable = page["revealable"]
    if not isinstance(revealable, list):
        _manifest_schema_error(label, f"{route_path}.revealable", "must be a JSON array")
    revealable_keys = {
        "downloads",
        "headings",
        "kind",
        "links",
        "location",
        "media",
        "tables",
        "text",
    }
    for index, disclosure in enumerate(revealable):
        disclosure_path = f"{route_path}.revealable[{index}]"
        disclosure = _exact_object(
            disclosure,
            revealable_keys,
            label=label,
            path=disclosure_path,
        )
        kind = _string(disclosure["kind"], label=label, path=f"{disclosure_path}.kind", nonempty=True)
        if re.fullmatch(r"[a-z][a-z0-9-]*", kind, re.ASCII) is None:
            _manifest_schema_error(label, f"{disclosure_path}.kind", "must be a normalized HTML tag")
        location = _string(
            disclosure["location"],
            label=label,
            path=f"{disclosure_path}.location",
            nonempty=True,
        )
        if location not in PAGE_LOCATIONS:
            _manifest_schema_error(label, f"{disclosure_path}.location", "has an unsupported page location")
        _string(disclosure["text"], label=label, path=f"{disclosure_path}.text")
        _validate_page_collections(
            {
                **disclosure,
                "form_controls": [],
                "redirects": [],
                "revealable": [],
                "runtime": [],
            },
            label=label,
            route_path=disclosure_path,
        )

    form_controls = page["form_controls"]
    if not isinstance(form_controls, list):
        _manifest_schema_error(label, f"{route_path}.form_controls", "must be a JSON array")
    form_keys = {
        "browsing_target",
        "checked",
        "disabled",
        "enctype",
        "form_associated",
        "form_owner",
        "location",
        "method",
        "multiple",
        "name",
        "no_validate",
        "readonly",
        "required",
        "selected",
        "tag",
        "target",
        "type",
        "value_digest",
    }
    for index, control in enumerate(form_controls):
        control_path = f"{route_path}.form_controls[{index}]"
        control = _exact_object(control, form_keys, label=label, path=control_path)
        boolean_form_keys = {
            "checked",
            "disabled",
            "form_associated",
            "multiple",
            "no_validate",
            "readonly",
            "required",
            "selected",
        }
        for key in boolean_form_keys:
            if type(control[key]) is not bool:
                _manifest_schema_error(label, f"{control_path}.{key}", "must be a JSON boolean")
        for key in form_keys - boolean_form_keys:
            _string(control[key], label=label, path=f"{control_path}.{key}")
        if control["value_digest"] and not SHA256_RE.fullmatch(control["value_digest"]):
            _manifest_schema_error(label, f"{control_path}.value_digest", "must be empty or a SHA-256 digest")
        if control["location"] not in PAGE_LOCATIONS:
            _manifest_schema_error(label, f"{control_path}.location", "has an unsupported page location")
        effective_fields = ("browsing_target", "enctype", "method", "target")
        if control["form_associated"]:
            if control["method"] not in FORM_METHODS:
                _manifest_schema_error(label, f"{control_path}.method", "has an unsupported effective method")
            if control["enctype"] not in FORM_ENCTYPES:
                _manifest_schema_error(label, f"{control_path}.enctype", "has an unsupported effective encoding")
            if not control["browsing_target"] or not control["target"]:
                _manifest_schema_error(label, control_path, "has incomplete effective form behavior")
        elif control["form_owner"] or control["no_validate"] or any(control[key] for key in effective_fields):
            _manifest_schema_error(label, control_path, "has effective behavior without an associated form")

    redirects = page["redirects"]
    if not isinstance(redirects, list):
        _manifest_schema_error(label, f"{route_path}.redirects", "must be a JSON array")
    for index, redirect in enumerate(redirects):
        redirect_path = f"{route_path}.redirects[{index}]"
        if not isinstance(redirect, dict) or set(redirect) not in ({"kind", "target"}, {"delay", "kind", "target"}):
            _manifest_schema_error(label, redirect_path, "has invalid redirect keys")
        if redirect["kind"] not in {"assign", "location", "meta-refresh", "replace"}:
            _manifest_schema_error(label, f"{redirect_path}.kind", "has an unsupported redirect kind")
        _string(redirect["target"], label=label, path=f"{redirect_path}.target", nonempty=True)
        if "delay" in redirect:
            _string(redirect["delay"], label=label, path=f"{redirect_path}.delay", nonempty=True)

    runtime = page["runtime"]
    if not isinstance(runtime, list):
        _manifest_schema_error(label, f"{route_path}.runtime", "must be a JSON array")
    for index, reference in enumerate(runtime):
        item_path = f"{route_path}.runtime[{index}]"
        reference = _exact_object(
            reference,
            {
                "as",
                "crossorigin",
                "fetchpriority",
                "href",
                "imagesizes",
                "imagesrcset",
                "integrity",
                "kind",
                "media",
                "nonce_digest",
                "referrerpolicy",
                "rel",
                "type",
            },
            label=label,
            path=item_path,
        )
        for key, value in reference.items():
            _string(value, label=label, path=f"{item_path}.{key}")
        if reference["kind"] not in REQUIRED_RUNTIME_ASSET_KINDS:
            _manifest_schema_error(
                label,
                f"{item_path}.kind",
                f"must be one of {sorted(REQUIRED_RUNTIME_ASSET_KINDS)}",
            )
        if reference["rel"] not in {"modulepreload", "preload", "script", "stylesheet"}:
            _manifest_schema_error(label, f"{item_path}.rel", "has an unsupported runtime relation")
        if reference["crossorigin"] not in {"", "anonymous", "use-credentials"}:
            _manifest_schema_error(label, f"{item_path}.crossorigin", "has an unsupported CORS mode")
        if reference["nonce_digest"] and not SHA256_RE.fullmatch(reference["nonce_digest"]):
            _manifest_schema_error(label, f"{item_path}.nonce_digest", "must be empty or a SHA-256 digest")


def _validate_grafana(page: dict[str, Any], *, label: str, route: str, route_path: str, origin: str) -> None:
    occurrences = page["grafana"]
    if not isinstance(occurrences, list):
        _manifest_schema_error(label, f"{route_path}.grafana", "must be a JSON array")
    occurrence_keys = {
        "fallback_url",
        "live_url",
        "location",
        "panel_id",
        "query",
        "source_digests",
        "source_roles",
        "source_status",
        "sources",
        "tag",
        "time_range",
        "uid",
        "variables",
        "visibility_states",
        "view_panel",
    }
    for index, occurrence in enumerate(occurrences):
        item_path = f"{route_path}.grafana[{index}]"
        occurrence = _exact_object(occurrence, occurrence_keys, label=label, path=item_path)
        for key in ("fallback_url", "live_url", "panel_id", "uid", "view_panel"):
            _string(occurrence[key], label=label, path=f"{item_path}.{key}")
        if not occurrence["uid"]:
            _manifest_schema_error(label, f"{item_path}.uid", "must be nonempty")
        location = _string(occurrence["location"], label=label, path=f"{item_path}.location")
        if location not in PAGE_LOCATIONS:
            _manifest_schema_error(label, f"{item_path}.location", f"must be one of {sorted(PAGE_LOCATIONS)}")
        _string_list_map(occurrence["query"], label=label, path=f"{item_path}.query")
        _string_list_map(occurrence["variables"], label=label, path=f"{item_path}.variables")
        for mapping_name in ("query", "variables"):
            for query_key, values in occurrence[mapping_name].items():
                if len(query_key.encode()) > GRAFANA_MAX_QUERY_KEY_BYTES or HLS_CREDENTIAL_QUERY_RE.search(query_key):
                    _manifest_schema_error(
                        label,
                        f"{item_path}.{mapping_name}",
                        "must contain only bounded non-credential query keys",
                    )
                if any(not SHA256_RE.fullmatch(value) for value in values):
                    _manifest_schema_error(
                        label,
                        f"{item_path}.{mapping_name}.{query_key}",
                        "must contain only protected SHA-256 values",
                    )
        time_range = occurrence["time_range"]
        if not isinstance(time_range, dict):
            _manifest_schema_error(label, f"{item_path}.time_range", "must be a JSON object")
        for key, value in time_range.items():
            if key not in {"from", "time", "time.window", "to"}:
                _manifest_schema_error(label, f"{item_path}.time_range", f"contains unsupported key {key!r}")
            _string(value, label=label, path=f"{item_path}.time_range.{key}")
            if not SHA256_RE.fullmatch(value):
                _manifest_schema_error(label, f"{item_path}.time_range.{key}", "must be a protected SHA-256 value")
        for key in ("panel_id", "view_panel"):
            if occurrence[key] and not SHA256_RE.fullmatch(occurrence[key]):
                _manifest_schema_error(label, f"{item_path}.{key}", "must be a protected SHA-256 value")
        sources = occurrence["sources"]
        if not isinstance(sources, dict) or not sources:
            _manifest_schema_error(label, f"{item_path}.sources", "must be a nonempty JSON object")
        for role, source in sources.items():
            if role not in GRAFANA_SOURCE_ATTRIBUTES:
                _manifest_schema_error(label, f"{item_path}.sources", f"contains unsupported role {role!r}")
            _string(source, label=label, path=f"{item_path}.sources.{role}", nonempty=True)
            sanitized, bounded = _sanitize_grafana_source(source)
            if source != sanitized or not bounded:
                _manifest_schema_error(
                    label,
                    f"{item_path}.sources.{role}",
                    "must be query-bounded, credential-free sanitized evidence",
                )
            parts = urlsplit(source)
            if any(not SHA256_RE.fullmatch(value) for _key, value in parse_qsl(parts.query, keep_blank_values=True)):
                _manifest_schema_error(
                    label,
                    f"{item_path}.sources.{role}",
                    "must not disclose Grafana query values",
                )
        source_digests = occurrence["source_digests"]
        if not isinstance(source_digests, dict) or set(source_digests) != set(sources):
            _manifest_schema_error(
                label,
                f"{item_path}.source_digests",
                "must contain exactly one digest for every source attribute",
            )
        for source_attribute, digest in source_digests.items():
            if not SHA256_RE.fullmatch(digest):
                _manifest_schema_error(
                    label,
                    f"{item_path}.source_digests.{source_attribute}",
                    "must be a SHA-256 digest",
                )
        source_roles = occurrence["source_roles"]
        if not isinstance(source_roles, dict) or set(source_roles) != set(sources):
            _manifest_schema_error(
                label,
                f"{item_path}.source_roles",
                "must contain exactly one role for every source attribute",
            )
        for source_attribute, role in source_roles.items():
            if role not in {"fallback", "live"}:
                _manifest_schema_error(
                    label,
                    f"{item_path}.source_roles.{source_attribute}",
                    "must be fallback or live",
                )
        source_status = occurrence["source_status"]
        if not isinstance(source_status, dict) or set(source_status) != set(sources):
            _manifest_schema_error(
                label,
                f"{item_path}.source_status",
                "must contain exactly one validation status for every source attribute",
            )
        live_statuses = {
            "active-live",
            "invalid-live-credential-query",
            "invalid-live-origin",
            "invalid-live-query-limits",
            "invalid-live-target",
        }
        fallback_statuses = {"invalid-fallback", "release-image"}
        for source_attribute, status in source_status.items():
            allowed_statuses = live_statuses if source_roles[source_attribute] == "live" else fallback_statuses
            if status not in allowed_statuses:
                _manifest_schema_error(
                    label,
                    f"{item_path}.source_status.{source_attribute}",
                    "does not match the declared source role",
                )
            source = sources[source_attribute]
            if (
                status == "active-live"
                and _grafana_live_source_status(
                    source,
                    source,
                    query_within_limits=True,
                )
                != "active-live"
            ):
                _manifest_schema_error(label, f"{item_path}.source_status.{source_attribute}", "is not active")
            if status == "release-image" and _grafana_fallback_source_status(source, source) != "release-image":
                _manifest_schema_error(
                    label,
                    f"{item_path}.source_status.{source_attribute}",
                    "is not a release-local image",
                )
        if not any(_grafana_url_target(source) for source in sources.values()):
            _manifest_schema_error(label, f"{item_path}.sources", "must contain a Grafana URL")
        states = _string_list(occurrence["visibility_states"], label=label, path=f"{item_path}.visibility_states")
        if unknown_states := sorted(set(states) - NATIVE_VISUAL_STATES):
            _manifest_schema_error(
                label,
                f"{item_path}.visibility_states",
                f"contains unsupported states {unknown_states}",
            )
        _string(occurrence["tag"], label=label, path=f"{item_path}.tag", nonempty=True)
        active_live = [
            sources[name]
            for name in (*GRAFANA_LIVE_SOURCE_ATTRIBUTES, "data-src", "src")
            if name in sources and source_roles[name] == "live" and source_status[name] == "active-live"
        ]
        release_fallback = [
            sources[name]
            for name in (*GRAFANA_FALLBACK_SOURCE_ATTRIBUTES, "src")
            if name in sources and source_roles[name] == "fallback" and source_status[name] == "release-image"
        ]
        expected_live = active_live[0] if active_live else ""
        expected_fallback = release_fallback[0] if release_fallback else ""
        if occurrence["live_url"] != expected_live or occurrence["fallback_url"] != expected_fallback:
            _manifest_schema_error(label, item_path, "does not select its validated live and fallback roles exactly")
        target = _grafana_url_target(expected_live or expected_fallback)
        if target is None:
            target = next(
                (_grafana_url_target(source) for source in sources.values() if _grafana_url_target(source)), None
            )
        if target is None:
            _manifest_schema_error(label, item_path, "does not contain reproducible Grafana target evidence")
        for key in ("panel_id", "query", "time_range", "uid", "variables", "view_panel"):
            if occurrence[key] != target[key]:
                _manifest_schema_error(label, f"{item_path}.{key}", "does not match the selected Grafana target")


def _validate_native_visibility(page: dict[str, Any], *, label: str, route_path: str) -> None:
    records = page["native_visibility"]
    if not isinstance(records, list):
        _manifest_schema_error(label, f"{route_path}.native_visibility", "must be a JSON array")
    keys = {
        "accessibility_states",
        "interactivity_states",
        "location",
        "own_accessibility_states",
        "own_interactivity_states",
        "own_visual_states",
        "tag",
        "visual_states",
    }
    for index, record in enumerate(records):
        item_path = f"{route_path}.native_visibility[{index}]"
        record = _exact_object(record, keys, label=label, path=item_path)
        _string(record["tag"], label=label, path=f"{item_path}.tag", nonempty=True)
        location = _string(record["location"], label=label, path=f"{item_path}.location")
        if location not in PAGE_LOCATIONS:
            _manifest_schema_error(label, f"{item_path}.location", f"must be one of {sorted(PAGE_LOCATIONS)}")
        visual_states = _string_list(record["visual_states"], label=label, path=f"{item_path}.visual_states")
        _string_list(record["own_visual_states"], label=label, path=f"{item_path}.own_visual_states")
        accessibility_states = _string_list(
            record["accessibility_states"], label=label, path=f"{item_path}.accessibility_states"
        )
        own_accessibility_states = _string_list(
            record["own_accessibility_states"],
            label=label,
            path=f"{item_path}.own_accessibility_states",
        )
        interactivity_states = _string_list(
            record["interactivity_states"], label=label, path=f"{item_path}.interactivity_states"
        )
        own_interactivity_states = _string_list(
            record["own_interactivity_states"],
            label=label,
            path=f"{item_path}.own_interactivity_states",
        )
        if not visual_states and not interactivity_states and not accessibility_states:
            _manifest_schema_error(label, item_path, "must record at least one native state")
        if unknown_states := sorted(set(visual_states) - NATIVE_VISUAL_STATES):
            _manifest_schema_error(label, f"{item_path}.visual_states", f"contains unsupported states {unknown_states}")
        if unknown_states := sorted(set(interactivity_states) - NATIVE_INTERACTIVITY_STATES):
            _manifest_schema_error(
                label,
                f"{item_path}.interactivity_states",
                f"contains unsupported states {unknown_states}",
            )
        if unknown_states := sorted(set(accessibility_states) - NATIVE_ACCESSIBILITY_STATES):
            _manifest_schema_error(
                label,
                f"{item_path}.accessibility_states",
                f"contains unsupported states {unknown_states}",
            )
        if not set(own_accessibility_states).issubset(accessibility_states):
            _manifest_schema_error(
                label,
                item_path,
                "own accessibility states must be included in propagated accessibility states",
            )
        if not set(own_interactivity_states).issubset(interactivity_states):
            _manifest_schema_error(
                label,
                item_path,
                "own interactivity states must be included in propagated interactivity states",
            )


def _validate_feature_documents(name: str, documents: Any, *, label: str, path: str) -> None:
    if not isinstance(documents, list):
        _manifest_schema_error(label, path, "must be a JSON array")
    if name not in {"robots", "rss", "sitemap"} and documents:
        _manifest_schema_error(label, path, f"must be empty for feature {name!r}")
    if name == "robots" and len(documents) > 1:
        _manifest_schema_error(label, path, "must contain at most the single /robots.txt document")
    seen_paths: set[str] = set()
    for document_index, document in enumerate(documents):
        document_path = f"{path}[{document_index}]"
        if name == "robots":
            document = _exact_object(
                document,
                {"groups", "path", "sitemaps"},
                label=label,
                path=document_path,
            )
            groups = document["groups"]
            if not isinstance(groups, list) or not groups:
                _manifest_schema_error(label, f"{document_path}.groups", "must be a nonempty JSON array")
            for group_index, group in enumerate(groups):
                group_path = f"{document_path}.groups[{group_index}]"
                group = _exact_object(group, {"directives", "user_agents"}, label=label, path=group_path)
                _string_list(group["user_agents"], label=label, path=f"{group_path}.user_agents", nonempty=True)
                directives = group["directives"]
                if not isinstance(directives, list) or not directives:
                    _manifest_schema_error(label, f"{group_path}.directives", "must be a nonempty JSON array")
                for directive_index, directive in enumerate(directives):
                    directive_path = f"{group_path}.directives[{directive_index}]"
                    directive = _exact_object(directive, {"name", "value"}, label=label, path=directive_path)
                    _string(directive["name"], label=label, path=f"{directive_path}.name", nonempty=True)
                    _string(directive["value"], label=label, path=f"{directive_path}.value")
            _string_list(document["sitemaps"], label=label, path=f"{document_path}.sitemaps", nonempty=True)
        elif name in {"rss", "sitemap"}:
            document = _exact_object(
                document,
                {"entries", "metadata", "path", "root"},
                label=label,
                path=document_path,
            )
            root = _string(document["root"], label=label, path=f"{document_path}.root", nonempty=True)
            allowed_roots = {"feed", "rss"} if name == "rss" else {"sitemapindex", "urlset"}
            if root not in allowed_roots:
                _manifest_schema_error(
                    label,
                    f"{document_path}.root",
                    f"must be one of {sorted(allowed_roots)}",
                )
            metadata_path = f"{document_path}.metadata"
            metadata_keys = {
                "authors",
                "categories",
                "description",
                "generator",
                "id",
                "language",
                "links",
                "namespace",
                "rights",
                "subtitle",
                "title",
                "updated",
                "url",
                "version",
                "xml_lang",
            }
            if name == "rss":
                metadata_keys.update({"canonical_digest", "canonical_elements"})
            metadata = _exact_object(
                document["metadata"],
                metadata_keys,
                label=label,
                path=metadata_path,
            )
            for key in (
                "description",
                "generator",
                "id",
                "language",
                "namespace",
                "rights",
                "subtitle",
                "title",
                "updated",
                "url",
                "version",
                "xml_lang",
            ):
                _string(metadata[key], label=label, path=f"{metadata_path}.{key}")
            if name == "rss":
                if SHA256_RE.fullmatch(metadata["canonical_digest"]) is None:
                    _manifest_schema_error(
                        label,
                        f"{metadata_path}.canonical_digest",
                        "must be a SHA-256 digest",
                    )
                canonical_elements = metadata["canonical_elements"]
                if not isinstance(canonical_elements, list):
                    _manifest_schema_error(
                        label,
                        f"{metadata_path}.canonical_elements",
                        "must be a JSON array",
                    )
                for canonical_index, canonical_element in enumerate(canonical_elements):
                    canonical_path = f"{metadata_path}.canonical_elements[{canonical_index}]"
                    canonical_element = _exact_object(
                        canonical_element,
                        {"digest", "tag_digest"},
                        label=label,
                        path=canonical_path,
                    )
                    for digest_field in ("digest", "tag_digest"):
                        if SHA256_RE.fullmatch(canonical_element[digest_field]) is None:
                            _manifest_schema_error(
                                label,
                                f"{canonical_path}.{digest_field}",
                                "must be a SHA-256 digest",
                            )
            expected_namespace = ATOM_NAMESPACE if root == "feed" else SITEMAP_NAMESPACE if name == "sitemap" else ""
            if metadata["namespace"] != expected_namespace:
                _manifest_schema_error(label, f"{metadata_path}.namespace", "does not match the exact root namespace")
            if metadata["version"] != ("2.0" if root == "rss" else ""):
                _manifest_schema_error(label, f"{metadata_path}.version", "does not match the exact root version")
            for collection_name in ("authors", "categories"):
                if not isinstance(metadata[collection_name], list):
                    _manifest_schema_error(
                        label,
                        f"{metadata_path}.{collection_name}",
                        "must be a JSON array",
                    )
            for author_index, author in enumerate(metadata["authors"]):
                author_path = f"{metadata_path}.authors[{author_index}]"
                author = _exact_object(author, {"email", "name", "uri"}, label=label, path=author_path)
                for key, value in author.items():
                    _string(value, label=label, path=f"{author_path}.{key}")
            for category_index, category in enumerate(metadata["categories"]):
                category_path = f"{metadata_path}.categories[{category_index}]"
                category = _exact_object(
                    category,
                    {"label", "scheme", "term"},
                    label=label,
                    path=category_path,
                )
                for key, value in category.items():
                    _string(value, label=label, path=f"{category_path}.{key}", nonempty=key == "term")
            if not isinstance(metadata["links"], list):
                _manifest_schema_error(label, f"{metadata_path}.links", "must be a JSON array")
            for link_index, link in enumerate(metadata["links"]):
                link_path = f"{metadata_path}.links[{link_index}]"
                link = _exact_object(
                    link,
                    {"href", "hreflang", "rel", "source", "title", "type"},
                    label=label,
                    path=link_path,
                )
                for key, value in link.items():
                    _string(value, label=label, path=f"{link_path}.{key}", nonempty=key in {"href", "rel"})
                if link["source"] not in {"atom-link", "rss-channel-link"}:
                    _manifest_schema_error(label, f"{link_path}.source", "has an unsupported feed link source")
            entries = document["entries"]
            if not isinstance(entries, list) or not entries:
                _manifest_schema_error(label, f"{document_path}.entries", "must be a nonempty JSON array")
            for entry_index, entry in enumerate(entries):
                entry_path = f"{document_path}.entries[{entry_index}]"
                if name == "sitemap":
                    entry = _exact_object(
                        entry,
                        {"changefreq", "lastmod", "priority", "url"},
                        label=label,
                        path=entry_path,
                    )
                    for key, value in entry.items():
                        _string(value, label=label, path=f"{entry_path}.{key}", nonempty=key == "url")
                else:
                    entry = _exact_object(
                        entry,
                        {
                            "authors",
                            "canonical_digest",
                            "categories",
                            "content",
                            "description",
                            "enclosures",
                            "effective_xml_lang",
                            "guid",
                            "id",
                            "published",
                            "summary",
                            "title",
                            "updated",
                            "url",
                        },
                        label=label,
                        path=entry_path,
                    )
                    required = {"id", "title", "updated", "url"} if root == "feed" else {"title", "url"}
                    for key in (
                        "content",
                        "description",
                        "effective_xml_lang",
                        "guid",
                        "id",
                        "published",
                        "summary",
                        "title",
                        "updated",
                        "url",
                    ):
                        _string(
                            entry[key],
                            label=label,
                            path=f"{entry_path}.{key}",
                            nonempty=key in required,
                        )
                    if SHA256_RE.fullmatch(entry["canonical_digest"]) is None:
                        _manifest_schema_error(
                            label,
                            f"{entry_path}.canonical_digest",
                            "must be a SHA-256 digest",
                        )
                    if not isinstance(entry["categories"], list):
                        _manifest_schema_error(label, f"{entry_path}.categories", "must be a JSON array")
                    for category_index, category in enumerate(entry["categories"]):
                        category_path = f"{entry_path}.categories[{category_index}]"
                        category = _exact_object(
                            category,
                            {"label", "scheme", "term"},
                            label=label,
                            path=category_path,
                        )
                        for key, value in category.items():
                            _string(
                                value,
                                label=label,
                                path=f"{category_path}.{key}",
                                nonempty=key == "term",
                            )
                    if not isinstance(entry["authors"], list):
                        _manifest_schema_error(label, f"{entry_path}.authors", "must be a JSON array")
                    for author_index, author in enumerate(entry["authors"]):
                        author_path = f"{entry_path}.authors[{author_index}]"
                        author = _exact_object(author, {"email", "name", "uri"}, label=label, path=author_path)
                        for key, value in author.items():
                            _string(value, label=label, path=f"{author_path}.{key}")
                    if not isinstance(entry["enclosures"], list):
                        _manifest_schema_error(label, f"{entry_path}.enclosures", "must be a JSON array")
                    for enclosure_index, enclosure in enumerate(entry["enclosures"]):
                        enclosure_path = f"{entry_path}.enclosures[{enclosure_index}]"
                        enclosure = _exact_object(
                            enclosure,
                            {"length", "type", "url"},
                            label=label,
                            path=enclosure_path,
                        )
                        for key, value in enclosure.items():
                            _string(
                                value,
                                label=label,
                                path=f"{enclosure_path}.{key}",
                                nonempty=key == "url",
                            )
        else:
            # Empty non-document feature arrays return before this branch.
            continue
        public_path = _string(document["path"], label=label, path=f"{document_path}.path", nonempty=True)
        if not public_path.startswith("/") or normalize_route(public_path) != public_path:
            _manifest_schema_error(label, f"{document_path}.path", "must be a normalized root-relative path")
        if public_path in seen_paths:
            _manifest_schema_error(label, path, f"contains duplicate document path {public_path!r}")
        if name == "robots" and public_path != "/robots.txt":
            _manifest_schema_error(label, f"{document_path}.path", "must equal /robots.txt")
        seen_paths.add(public_path)


def _validate_features(manifest: dict[str, Any], *, label: str) -> None:
    features = manifest["features"]
    if not isinstance(features, dict) or set(features) != set(FEATURE_NAMES):
        _manifest_schema_error(label, "features", f"must contain exactly {list(FEATURE_NAMES)}")
    evidence_keys = {
        "controls",
        "documents",
        "index_assets",
        "index_documents",
        "markup",
        "routes",
        "runtime_assets",
        "suppressed_routes",
    }
    for name, feature in features.items():
        feature_path = f"features.{name}"
        feature = _exact_object(
            feature,
            {"evidence", "present", "verification"},
            label=label,
            path=feature_path,
        )
        if type(feature["present"]) is not bool:
            _manifest_schema_error(label, f"{feature_path}.present", "must be a JSON boolean")
        if feature["verification"] != "structural-only":
            _manifest_schema_error(label, f"{feature_path}.verification", "must equal 'structural-only'")
        evidence = _exact_object(feature["evidence"], evidence_keys, label=label, path=f"{feature_path}.evidence")
        evidence_path = f"{feature_path}.evidence"
        for key in ("controls", "index_assets", "markup", "runtime_assets"):
            _string_list(evidence[key], label=label, path=f"{evidence_path}.{key}")
        index_documents = evidence["index_documents"]
        if not isinstance(index_documents, list):
            _manifest_schema_error(label, f"{evidence_path}.index_documents", "must be a JSON array")
        if name != "search" and index_documents:
            _manifest_schema_error(label, f"{evidence_path}.index_documents", "must be empty outside search")
        for index, document in enumerate(index_documents):
            document_path = f"{evidence_path}.index_documents[{index}]"
            document = _exact_object(
                document,
                {"descriptors", "entry_count", "format", "languages", "path"},
                label=label,
                path=document_path,
            )
            _integer(document["entry_count"], label=label, path=f"{document_path}.entry_count", minimum=1)
            if document["format"] not in {"content-index-json", "pagefind-binary", "pagefind-entry"}:
                _manifest_schema_error(label, f"{document_path}.format", "has an unsupported search-index format")
            _string(document["path"], label=label, path=f"{document_path}.path", nonempty=True)
            _string_list(document["languages"], label=label, path=f"{document_path}.languages")
            descriptors = document["descriptors"]
            if not isinstance(descriptors, list):
                _manifest_schema_error(label, f"{document_path}.descriptors", "must be a JSON array")
            if document["format"] == "pagefind-entry" and len(descriptors) != document["entry_count"]:
                _manifest_schema_error(
                    label,
                    f"{document_path}.descriptors",
                    "must exactly describe every Pagefind language",
                )
            if document["format"] != "pagefind-entry" and descriptors:
                _manifest_schema_error(label, f"{document_path}.descriptors", "must be empty for this format")
            for descriptor_index, descriptor in enumerate(descriptors):
                descriptor_path = f"{document_path}.descriptors[{descriptor_index}]"
                descriptor = _exact_object(
                    descriptor,
                    {
                        "descriptor_digest",
                        "hash",
                        "language",
                        "page_count",
                        "shards",
                        "wasm",
                        "wasm_asset",
                    },
                    label=label,
                    path=descriptor_path,
                )
                if not SHA256_RE.fullmatch(descriptor["descriptor_digest"]):
                    _manifest_schema_error(
                        label,
                        f"{descriptor_path}.descriptor_digest",
                        "must be a SHA-256 digest",
                    )
                for key in ("hash", "language", "wasm"):
                    _string(descriptor[key], label=label, path=f"{descriptor_path}.{key}", nonempty=True)
                _integer(descriptor["page_count"], label=label, path=f"{descriptor_path}.page_count", minimum=1)
                if descriptor["descriptor_digest"] != canonical_digest(
                    {
                        "hash": descriptor["hash"],
                        "language": descriptor["language"],
                        "page_count": descriptor["page_count"],
                        "wasm": descriptor["wasm"],
                    }
                ):
                    _manifest_schema_error(
                        label,
                        f"{descriptor_path}.descriptor_digest",
                        "does not match the exact Pagefind descriptor schema",
                    )
                shards = descriptor["shards"]
                if (
                    not isinstance(shards, list)
                    or len(shards) < 2
                    or sum(1 for shard in shards if isinstance(shard, dict) and shard.get("format") == "pf_meta") != 1
                    or not any(isinstance(shard, dict) and shard.get("format") == "pf_index" for shard in shards)
                ):
                    _manifest_schema_error(
                        label,
                        f"{descriptor_path}.shards",
                        "must include exactly one Pagefind metadata shard and at least one index shard",
                    )
                for shard_index, shard in enumerate(shards):
                    shard_path = f"{descriptor_path}.shards[{shard_index}]"
                    shard = _exact_object(
                        shard,
                        {"format", "path", "sha256", "size"},
                        label=label,
                        path=shard_path,
                    )
                    if shard["format"] not in {"pf_fragment", "pf_index", "pf_meta"}:
                        _manifest_schema_error(label, f"{shard_path}.format", "has an unsupported shard format")
                    public_path = _string(shard["path"], label=label, path=f"{shard_path}.path", nonempty=True)
                    document_parent = Path(document["path"]).parent
                    public = Path(public_path)
                    if shard["format"] == "pf_meta":
                        valid_path = public.parent == document_parent and public.name in {
                            f"pagefind.{descriptor['hash']}.pf_meta",
                            f"pagefind.{descriptor['language']}_{descriptor['hash']}.pf_meta",
                        }
                    elif shard["format"] == "pf_index":
                        valid_path = (
                            public.parent == document_parent
                            and public.name == f"pagefind.{descriptor['language']}_{descriptor['hash']}.pf_index"
                        ) or (
                            public.parent == document_parent / "index"
                            and re.fullmatch(
                                rf"{re.escape(descriptor['language'])}_[A-Za-z0-9_-]{{6,128}}\.pf_index",
                                public.name,
                            )
                            is not None
                        )
                    else:
                        valid_path = (
                            public.parent == document_parent / "fragment"
                            and re.fullmatch(
                                rf"{re.escape(descriptor['language'])}_[A-Za-z0-9_-]{{6,128}}\.pf_fragment",
                                public.name,
                            )
                            is not None
                        )
                    if not valid_path:
                        _manifest_schema_error(
                            label,
                            f"{shard_path}.path",
                            "must match the bounded Pagefind language, format, and versioned shard layout",
                        )
                    _integer(shard["size"], label=label, path=f"{shard_path}.size", minimum=1)
                    if not re.fullmatch(r"[0-9a-f]{64}", shard["sha256"]):
                        _manifest_schema_error(label, f"{shard_path}.sha256", "must be a lowercase SHA-256 digest")
                    asset = manifest["assets"].get(public_path)
                    if (
                        not asset
                        or not asset["exists"]
                        or asset["size"] != shard["size"]
                        or asset["sha256"] != shard["sha256"]
                        or not any(reference["kind"] == "search-index" for reference in asset["references"])
                    ):
                        _manifest_schema_error(
                            label,
                            shard_path,
                            "must match an existing typed regular bounded shard asset",
                        )
                wasm_path = f"{descriptor_path}.wasm_asset"
                wasm_asset = _exact_object(
                    descriptor["wasm_asset"],
                    {"path", "sha256", "size"},
                    label=label,
                    path=wasm_path,
                )
                public_wasm_path = _string(
                    wasm_asset["path"],
                    label=label,
                    path=f"{wasm_path}.path",
                    nonempty=True,
                )
                expected_wasm_name = f"wasm.{descriptor['wasm']}.pagefind"
                if (
                    Path(public_wasm_path).name != expected_wasm_name
                    or Path(public_wasm_path).parent != Path(document["path"]).parent
                ):
                    _manifest_schema_error(
                        label,
                        f"{wasm_path}.path",
                        "must exactly match the descriptor-selected Pagefind WebAssembly path",
                    )
                _integer(wasm_asset["size"], label=label, path=f"{wasm_path}.size", minimum=1)
                if not re.fullmatch(r"[0-9a-f]{64}", wasm_asset["sha256"]):
                    _manifest_schema_error(
                        label,
                        f"{wasm_path}.sha256",
                        "must be a lowercase SHA-256 digest",
                    )
                manifest_wasm = manifest["assets"].get(public_wasm_path)
                if (
                    not manifest_wasm
                    or not manifest_wasm["exists"]
                    or manifest_wasm["size"] != wasm_asset["size"]
                    or manifest_wasm["sha256"] != wasm_asset["sha256"]
                    or not any(reference["kind"] == "search-runtime" for reference in manifest_wasm["references"])
                ):
                    _manifest_schema_error(
                        label,
                        wasm_path,
                        "must match the descriptor-selected typed Pagefind WebAssembly asset",
                    )
            if document["format"] == "pagefind-entry" and sorted(
                descriptor["language"] for descriptor in descriptors
            ) != sorted(document["languages"]):
                _manifest_schema_error(
                    label,
                    f"{document_path}.descriptors",
                    "must map one-to-one to the exact Pagefind language keys",
                )
        if name == "search":
            document_paths = {document["path"] for document in index_documents}
            if document_paths != set(evidence["index_assets"]):
                _manifest_schema_error(
                    label,
                    f"{evidence_path}.index_documents",
                    "must exactly describe every search index asset",
                )
            for asset_path in evidence["index_assets"]:
                asset = manifest["assets"].get(asset_path)
                if not asset or not any(reference["kind"] == "search-index" for reference in asset["references"]):
                    _manifest_schema_error(
                        label, f"{evidence_path}.index_assets", f"lacks typed asset evidence for {asset_path}"
                    )
            for asset_path in evidence["runtime_assets"]:
                asset = manifest["assets"].get(asset_path)
                if asset and not any(
                    reference["kind"] in {"search-runtime", "module", "script"} for reference in asset["references"]
                ):
                    _manifest_schema_error(
                        label,
                        f"{evidence_path}.runtime_assets",
                        f"lacks typed runtime evidence for {asset_path}",
                    )
        _validate_feature_documents(
            name,
            evidence["documents"],
            label=label,
            path=f"{evidence_path}.documents",
        )
        for map_name in ("routes", "suppressed_routes"):
            route_map = evidence[map_name]
            if not isinstance(route_map, dict):
                _manifest_schema_error(label, f"{evidence_path}.{map_name}", "must be a JSON object")
            for route, items in route_map.items():
                if not isinstance(route, str) or route != normalize_route(route) or route not in manifest["routes"]:
                    _manifest_schema_error(
                        label,
                        f"{evidence_path}.{map_name}",
                        f"contains unknown or non-normalized route {route!r}",
                    )
                _string_list(items, label=label, path=f"{evidence_path}.{map_name}.{route}")


def _validate_integrity_finding(finding: Any, *, label: str, path: str) -> None:
    if not isinstance(finding, dict) or not isinstance(finding.get("code"), str):
        _manifest_schema_error(label, path, "must be a JSON object with a string code")
    code = finding["code"]
    path_only = {
        "empty-robots",
        "empty-rss",
        "empty-sitemap",
        "invalid-search-index",
        "invalid-search-runtime",
        "unsafe-tree-escaping-path",
        "unsafe-tree-html-directory",
        "unsafe-tree-hardlink",
        "unsafe-tree-sparse-file",
        "unsafe-tree-special-file",
        "unsafe-tree-symlink",
    }
    path_detail = {
        "invalid-robots",
        "invalid-rss",
        "invalid-sitemap",
        "invalid-xml-url",
        "unsafe-tree-stat-error",
        "unsafe-tree-unreadable-directory",
    }
    if code in path_only:
        finding = _exact_object(finding, {"code", "path"}, label=label, path=path)
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
    elif code in path_detail:
        finding = _exact_object(finding, {"code", "detail", "path"}, label=label, path=path)
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
        _string(finding["detail"], label=label, path=f"{path}.detail")
    elif code == "credential-bearing-url":
        finding = _exact_object(
            finding,
            {"code", "path", "reference_digest", "resource"},
            label=label,
            path=path,
        )
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
        if not SHA256_RE.fullmatch(finding["reference_digest"]):
            _manifest_schema_error(label, f"{path}.reference_digest", "must be a SHA-256 digest")
        if finding["resource"] not in {"robots", "xml"}:
            _manifest_schema_error(label, f"{path}.resource", "has an unsupported URL resource")
    elif code in {"invalid-rss-entry", "invalid-sitemap-entry"}:
        finding = _exact_object(
            finding,
            {"code", "entry_index", "missing", "path"},
            label=label,
            path=path,
        )
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
        _integer(finding["entry_index"], label=label, path=f"{path}.entry_index", minimum=0)
        _string_list(finding["missing"], label=label, path=f"{path}.missing", nonempty=True)
    elif code == "rss-route-mismatch":
        finding = _exact_object(finding, {"code", "invalid", "path"}, label=label, path=path)
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
        _string_list(finding["invalid"], label=label, path=f"{path}.invalid", nonempty=True)
    elif code == "robots-sitemap-mismatch":
        finding = _exact_object(finding, {"code", "invalid", "path"}, label=label, path=path)
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
        _string_list(finding["invalid"], label=label, path=f"{path}.invalid", nonempty=True)
    elif code == "sitemap-route-mismatch":
        finding = _exact_object(finding, {"code", "extra", "missing", "path"}, label=label, path=path)
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
        missing = _string_list(finding["missing"], label=label, path=f"{path}.missing")
        extra = _string_list(finding["extra"], label=label, path=f"{path}.extra")
        if not missing and not extra:
            _manifest_schema_error(label, path, "must report at least one missing or extra URL")
    elif code == "sitemap-child-invalid":
        finding = _exact_object(
            finding,
            {"code", "path", "reason", "reference_digest", "target_path"},
            label=label,
            path=path,
        )
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
        _string(finding["target_path"], label=label, path=f"{path}.target_path")
        if finding["reason"] not in {"missing-or-invalid-child", "nonlocal"}:
            _manifest_schema_error(label, f"{path}.reason", "has an unsupported sitemap-child reason")
        if not SHA256_RE.fullmatch(finding["reference_digest"]):
            _manifest_schema_error(label, f"{path}.reference_digest", "must be a SHA-256 digest")
    elif code == "sitemap-cycle":
        finding = _exact_object(finding, {"code", "paths"}, label=label, path=path)
        _string_list(finding["paths"], label=label, path=f"{path}.paths", nonempty=True)
    elif code == "sitemap-orphan":
        finding = _exact_object(finding, {"code", "path"}, label=label, path=path)
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
    elif code in {"duplicate-alias", "duplicate-route"}:
        finding = _exact_object(finding, {"code", "route", "sources"}, label=label, path=path)
        route = _string(finding["route"], label=label, path=f"{path}.route", nonempty=True)
        if route != normalize_route(route):
            _manifest_schema_error(label, f"{path}.route", "must be normalized")
        _string_list(finding["sources"], label=label, path=f"{path}.sources", nonempty=True)
    elif code == "invalid-alias":
        finding = _exact_object(finding, {"code", "route", "violations"}, label=label, path=path)
        route = _string(finding["route"], label=label, path=f"{path}.route", nonempty=True)
        if route != normalize_route(route):
            _manifest_schema_error(label, f"{path}.route", "must be normalized")
        violations = _string_list(finding["violations"], label=label, path=f"{path}.violations", nonempty=True)
        allowed = {
            "alias-or-dangling-target",
            "canonical-mismatch",
            "cycle",
            "dangling-target",
            "missing-follow",
            "missing-noindex",
            "nonlocal-target",
            "self-target",
            "target-not-canonical-route",
        }
        if unknown := sorted(set(violations) - allowed):
            _manifest_schema_error(label, f"{path}.violations", f"contains unsupported values {unknown}")
    elif code == "invalid-hls-playlist":
        finding = _exact_object(finding, {"code", "detail", "line", "path"}, label=label, path=path)
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
        _integer(finding["line"], label=label, path=f"{path}.line", minimum=0)
        _string(finding["detail"], label=label, path=f"{path}.detail", nonempty=True)
    elif code == "hls-unsafe-url":
        finding = _exact_object(
            finding,
            {"code", "line", "path", "reason", "reference_digest", "role"},
            label=label,
            path=path,
        )
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
        _integer(finding["line"], label=label, path=f"{path}.line", minimum=1)
        if not SHA256_RE.fullmatch(finding["reference_digest"]):
            _manifest_schema_error(label, f"{path}.reference_digest", "must be a SHA-256 digest")
        if finding["reason"] not in {
            "empty",
            "invalid",
            "nonlocal",
            "query-credential",
            "traversal",
            "userinfo-credential",
        }:
            _manifest_schema_error(label, f"{path}.reason", "has an unsupported HLS URL reason")
        if finding["role"] not in HLS_DEPENDENCY_ROLES:
            _manifest_schema_error(label, f"{path}.role", "has an unsupported HLS dependency role")
    elif code == "hls-cycle":
        finding = _exact_object(finding, {"code", "paths"}, label=label, path=path)
        _string_list(finding["paths"], label=label, path=f"{path}.paths", nonempty=True)
    elif code == "hls-target-type-mismatch":
        finding = _exact_object(
            finding,
            {"code", "line", "path", "reason", "role", "target_path"},
            label=label,
            path=path,
        )
        _integer(finding["line"], label=label, path=f"{path}.line", minimum=1)
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
        _string(finding["target_path"], label=label, path=f"{path}.target_path", nonempty=True)
        if finding["role"] not in HLS_DEPENDENCY_ROLES:
            _manifest_schema_error(label, f"{path}.role", "has an unsupported HLS dependency role")
        if finding["reason"] not in {
            "extension-role-mismatch",
            "payload-media-type-mismatch",
            "media-playlist-role-required",
            "playlist-role-required",
            "subtitle-content-missing",
            "subtitle-segment-type-mismatch",
            "target-not-parsed-playlist",
        }:
            _manifest_schema_error(label, f"{path}.reason", "has an unsupported target-type reason")
    elif code == "hls-orphan":
        finding = _exact_object(finding, {"code", "kind", "path"}, label=label, path=path)
        if finding["kind"] not in {"media", "playlist"}:
            _manifest_schema_error(label, f"{path}.kind", "must be media or playlist")
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
    elif code == "limit-exceeded":
        finding = _exact_object(finding, {"code", "detail", "path", "resource"}, label=label, path=path)
        _string(finding["detail"], label=label, path=f"{path}.detail", nonempty=True)
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
        if finding["resource"] not in {
            "asset",
            "hls",
            "html",
            "json",
            "manifest",
            "robots",
            "stage-boundary",
            "tree",
            "xml",
        }:
            _manifest_schema_error(label, f"{path}.resource", "has an unsupported limited resource")
    elif code == "invalid-html":
        finding = _exact_object(finding, {"code", "detail", "path"}, label=label, path=path)
        _string(finding["detail"], label=label, path=f"{path}.detail")
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
    elif code == "unsafe-tree-read-error":
        finding = _exact_object(finding, {"code", "detail", "path"}, label=label, path=path)
        _string(finding["detail"], label=label, path=f"{path}.detail", nonempty=True)
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
    elif code == "duplicate-html-attribute":
        finding = _exact_object(
            finding,
            {"attribute", "code", "column", "line", "route", "tag"},
            label=label,
            path=path,
        )
        _string(finding["attribute"], label=label, path=f"{path}.attribute", nonempty=True)
        _string(finding["tag"], label=label, path=f"{path}.tag", nonempty=True)
        _integer(finding["line"], label=label, path=f"{path}.line", minimum=1)
        _integer(finding["column"], label=label, path=f"{path}.column", minimum=0)
        route = _string(finding["route"], label=label, path=f"{path}.route", nonempty=True)
        if route != normalize_route(route):
            _manifest_schema_error(label, f"{path}.route", "must be normalized")
    elif code == "invalid-runtime-reference":
        finding = _exact_object(
            finding,
            {"code", "index", "reasons", "route"},
            label=label,
            path=path,
        )
        _integer(finding["index"], label=label, path=f"{path}.index", minimum=0)
        reasons = _string_list(finding["reasons"], label=label, path=f"{path}.reasons", nonempty=True)
        allowed = {
            "image-type-mismatch",
            "nonlocal-imagesrcset",
            "unsupported-image-extension",
            "unsupported-imagesrcset-extension",
        }
        if unknown := sorted(set(reasons) - allowed):
            _manifest_schema_error(label, f"{path}.reasons", f"contains unsupported values {unknown}")
        route = _string(finding["route"], label=label, path=f"{path}.route", nonempty=True)
        if route != normalize_route(route):
            _manifest_schema_error(label, f"{path}.route", "must be normalized")
    elif code == "grafana-source-conflict":
        finding = _exact_object(
            finding,
            {"code", "conflicts", "location", "route", "source_digests"},
            label=label,
            path=path,
        )
        conflicts = _string_list(finding["conflicts"], label=label, path=f"{path}.conflicts", nonempty=True)
        if unknown := sorted(
            set(conflicts)
            - {
                "fallback_not_release_image",
                "fallback_url",
                "live_render",
                "live_credential_query",
                "live_origin",
                "live_query_limits",
                "live_target",
                "live_url",
                "panel",
                "role_url",
                "time_range",
                "uid",
                "variables",
            }
        ):
            _manifest_schema_error(label, f"{path}.conflicts", f"contains unsupported conflicts {unknown}")
        location = _string(finding["location"], label=label, path=f"{path}.location")
        if location not in PAGE_LOCATIONS:
            _manifest_schema_error(label, f"{path}.location", f"must be one of {sorted(PAGE_LOCATIONS)}")
        route = _string(finding["route"], label=label, path=f"{path}.route", nonempty=True)
        if route != normalize_route(route):
            _manifest_schema_error(label, f"{path}.route", "must be normalized")
        source_digests = finding["source_digests"]
        if not isinstance(source_digests, dict) or not source_digests:
            _manifest_schema_error(label, f"{path}.source_digests", "must be a nonempty JSON object")
        for role, source_digest in source_digests.items():
            if role not in GRAFANA_SOURCE_ATTRIBUTES:
                _manifest_schema_error(label, f"{path}.source_digests", f"contains unsupported role {role!r}")
            if not SHA256_RE.fullmatch(source_digest):
                _manifest_schema_error(label, f"{path}.source_digests.{role}", "must be a SHA-256 digest")
    elif code in {"grafana-fallback-missing", "grafana-live-missing"}:
        finding = _exact_object(
            finding,
            {"code", "location", "route", "source_digests"},
            label=label,
            path=path,
        )
        location = _string(finding["location"], label=label, path=f"{path}.location")
        if location not in PAGE_LOCATIONS:
            _manifest_schema_error(label, f"{path}.location", f"must be one of {sorted(PAGE_LOCATIONS)}")
        route = _string(finding["route"], label=label, path=f"{path}.route", nonempty=True)
        if route != normalize_route(route):
            _manifest_schema_error(label, f"{path}.route", "must be normalized")
        source_digests = finding["source_digests"]
        if not isinstance(source_digests, dict) or not source_digests:
            _manifest_schema_error(label, f"{path}.source_digests", "must be a nonempty JSON object")
        for role, source_digest in source_digests.items():
            if role not in GRAFANA_SOURCE_ATTRIBUTES:
                _manifest_schema_error(label, f"{path}.source_digests", f"contains unsupported role {role!r}")
            if not SHA256_RE.fullmatch(source_digest):
                _manifest_schema_error(label, f"{path}.source_digests.{role}", "must be a SHA-256 digest")
    elif code == "grafana-fallback-invalid":
        finding = _exact_object(
            finding,
            {"code", "location", "path", "reason", "reference_digest", "route", "source_attribute"},
            label=label,
            path=path,
        )
        if finding["location"] not in PAGE_LOCATIONS:
            _manifest_schema_error(label, f"{path}.location", "has an unsupported page location")
        _string(finding["path"], label=label, path=f"{path}.path", nonempty=True)
        if finding["reason"] not in {"invalid-image", "missing", "not-regular"}:
            _manifest_schema_error(label, f"{path}.reason", "has an unsupported fallback failure reason")
        if not SHA256_RE.fullmatch(finding["reference_digest"]):
            _manifest_schema_error(label, f"{path}.reference_digest", "must be a SHA-256 digest")
        route = _string(finding["route"], label=label, path=f"{path}.route", nonempty=True)
        if route != normalize_route(route):
            _manifest_schema_error(label, f"{path}.route", "must be normalized")
        if finding["source_attribute"] not in GRAFANA_SOURCE_ATTRIBUTES:
            _manifest_schema_error(label, f"{path}.source_attribute", "has an unsupported source attribute")
    else:
        _manifest_schema_error(label, f"{path}.code", f"contains unsupported finding code {code!r}")


def _validate_hls(manifest: dict[str, Any], *, label: str) -> None:
    hls = _exact_object(manifest["hls"], {"playlists", "policy", "roots"}, label=label, path="hls")
    if not _matches_canonical_authority(hls["policy"], _HLS_POLICY_AUTHORITY_JSON):
        _manifest_schema_error(label, "hls.policy", "must exactly match the schema v1 local-closure policy")
    playlists = hls["playlists"]
    if not isinstance(playlists, dict):
        _manifest_schema_error(label, "hls.playlists", "must be a JSON object")
    raw_integrity = manifest.get("integrity")
    raw_findings = raw_integrity.get("findings", []) if isinstance(raw_integrity, dict) else []
    if not isinstance(raw_findings, list):
        raw_findings = []
    for playlist_path, playlist in playlists.items():
        if (
            not isinstance(playlist_path, str)
            or not playlist_path.startswith("/")
            or Path(urlsplit(playlist_path).path).suffix.lower() not in HLS_PLAYLIST_SUFFIXES
        ):
            _manifest_schema_error(label, "hls.playlists", f"contains invalid path {playlist_path!r}")
        record_path = f"hls.playlists.{playlist_path}"
        playlist = _exact_object(
            playlist,
            {"dateranges", "dependencies", "kind"},
            label=label,
            path=record_path,
        )
        if playlist["kind"] not in {"invalid", "master", "media"}:
            _manifest_schema_error(label, f"{record_path}.kind", "must be invalid, master, or media")
        dateranges = playlist["dateranges"]
        if not isinstance(dateranges, list):
            _manifest_schema_error(label, f"{record_path}.dateranges", "must be a JSON array")
        for daterange_index, daterange in enumerate(dateranges):
            daterange_path = f"{record_path}.dateranges[{daterange_index}]"
            daterange = _exact_object(
                daterange,
                {"attributes", "line"},
                label=label,
                path=daterange_path,
            )
            _integer(daterange["line"], label=label, path=f"{daterange_path}.line", minimum=1)
            attributes = daterange["attributes"]
            if not isinstance(attributes, list) or not attributes:
                _manifest_schema_error(
                    label,
                    f"{daterange_path}.attributes",
                    "must be a nonempty JSON array",
                )
            seen_names: set[str] = set()
            retained_attributes: dict[str, str] = {}
            retained_quoted: dict[str, bool] = {}
            uri_looking_extension = False
            for attribute_index, attribute in enumerate(attributes):
                attribute_path = f"{daterange_path}.attributes[{attribute_index}]"
                attribute = _exact_object(
                    attribute,
                    {"name", "quoted", "value", "value_digest"},
                    label=label,
                    path=attribute_path,
                )
                name = _string(attribute["name"], label=label, path=f"{attribute_path}.name", nonempty=True)
                if HLS_ATTRIBUTE_NAME_RE.fullmatch(name) is None or name in seen_names:
                    _manifest_schema_error(label, f"{attribute_path}.name", "must be a unique HLS attribute name")
                seen_names.add(name)
                if type(attribute["quoted"]) is not bool:
                    _manifest_schema_error(label, f"{attribute_path}.quoted", "must be a JSON boolean")
                value = _string(attribute["value"], label=label, path=f"{attribute_path}.value")
                value_digest = _string(
                    attribute["value_digest"],
                    label=label,
                    path=f"{attribute_path}.value_digest",
                )
                if value_digest:
                    if value or SHA256_RE.fullmatch(value_digest) is None:
                        _manifest_schema_error(
                            label,
                            attribute_path,
                            "must protect a credential value with only its SHA-256 digest",
                        )
                retained_attributes[name] = value
                retained_quoted[name] = attribute["quoted"]
                if name.startswith("X-"):
                    uri_looking, _credentialed = _hls_x_client_uri_status(value)
                    uri_looking_extension = uri_looking_extension or uri_looking or bool(value_digest)
            expected_details = []
            if semantic_error := _hls_attribute_semantic_error(
                "#EXT-X-DATERANGE",
                retained_attributes,
                retained_quoted,
            ):
                expected_details.append(semantic_error)
            if uri_looking_extension:
                expected_details.append("#EXT-X-DATERANGE client attribute has an unsupported URI-looking value")
            available_details = {
                finding.get("detail")
                for finding in raw_findings
                if isinstance(finding, dict)
                and finding.get("code") == "invalid-hls-playlist"
                and finding.get("path") == playlist_path
                and finding.get("line") == daterange["line"]
            }
            if not set(expected_details).issubset(available_details):
                _manifest_schema_error(
                    label,
                    daterange_path,
                    "must retain its recomputed DATERANGE integrity findings",
                )
        dependencies = playlist["dependencies"]
        if not isinstance(dependencies, list):
            _manifest_schema_error(label, f"{record_path}.dependencies", "must be a JSON array")
        playlist_asset = manifest["assets"].get(playlist_path)
        if not playlist_asset or not any(
            reference["kind"] == "hls-playlist" for reference in playlist_asset["references"]
        ):
            _manifest_schema_error(label, record_path, "must have a typed hls-playlist asset record")
        for index, dependency in enumerate(dependencies):
            dependency_path = f"{record_path}.dependencies[{index}]"
            dependency = _exact_object(
                dependency,
                {"line", "reason", "reference_digest", "role", "url"},
                label=label,
                path=dependency_path,
            )
            _integer(dependency["line"], label=label, path=f"{dependency_path}.line", minimum=1)
            if not SHA256_RE.fullmatch(dependency["reference_digest"]):
                _manifest_schema_error(label, f"{dependency_path}.reference_digest", "must be a SHA-256 digest")
            reason = _string(dependency["reason"], label=label, path=f"{dependency_path}.reason")
            if reason not in {
                "",
                "empty",
                "invalid",
                "nonlocal",
                "query-credential",
                "traversal",
                "userinfo-credential",
            }:
                _manifest_schema_error(label, f"{dependency_path}.reason", "is unsupported")
            _string(dependency["url"], label=label, path=f"{dependency_path}.url", nonempty=not reason)
            if reason and dependency["url"]:
                _manifest_schema_error(label, f"{dependency_path}.url", "must be empty for an unsafe reference")
            if dependency["role"] not in HLS_DEPENDENCY_ROLES:
                _manifest_schema_error(label, f"{dependency_path}.role", "has an unsupported HLS dependency role")
            if not reason:
                parts = urlsplit(dependency["url"])
                if parts.scheme or parts.netloc or not parts.path.startswith("/"):
                    _manifest_schema_error(label, f"{dependency_path}.url", "must be release-local")
                if (
                    normalize_reference(
                        dependency["url"],
                        route=playlist_path,
                        origin=manifest["origin"],
                    )
                    != dependency["url"]
                ):
                    _manifest_schema_error(
                        label,
                        f"{dependency_path}.url",
                        "must retain exact normalized percent and query ordering",
                    )
                decoded_path = unquote(parts.path)
                if "\\" in decoded_path or ".." in decoded_path.split("/"):
                    _manifest_schema_error(label, f"{dependency_path}.url", "must not contain traversal")
                if any(
                    HLS_CREDENTIAL_QUERY_RE.search(key)
                    for key, _value in parse_qsl(parts.query, keep_blank_values=True)
                ):
                    _manifest_schema_error(
                        label,
                        f"{dependency_path}.url",
                        "must not disclose a protected query category",
                    )
                asset_path, asset_kind = parts.path, f"hls-{dependency['role']}"
                asset = manifest["assets"].get(asset_path)
                if not asset or not any(item["kind"] == asset_kind for item in asset["references"]):
                    _manifest_schema_error(
                        label,
                        dependency_path,
                        f"must have a typed {asset_kind} asset record for {asset_path}",
                    )
    roots = _string_list(hls["roots"], label=label, path="hls.roots")
    for index, root in enumerate(roots):
        if root not in playlists:
            _manifest_schema_error(label, f"hls.roots[{index}]", "must name a parsed playlist")
    expected_roots = sorted(
        path
        for path, asset in manifest["assets"].items()
        if Path(urlsplit(path).path).suffix.lower() in HLS_PLAYLIST_SUFFIXES
        and any(reference["route"] for reference in asset["references"])
    )
    if roots != expected_roots:
        _manifest_schema_error(label, "hls.roots", "must exactly equal route-referenced playlist assets")


def _hls_url_findings_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for playlist_path, playlist in manifest["hls"]["playlists"].items():
        for dependency in playlist["dependencies"]:
            if dependency["reason"]:
                findings.append(
                    {
                        "code": "hls-unsafe-url",
                        "line": dependency["line"],
                        "path": playlist_path,
                        "reason": dependency["reason"],
                        "reference_digest": dependency["reference_digest"],
                        "role": dependency["role"],
                    }
                )
    return sorted(findings, key=canonical_json)


def _grafana_findings_from_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for route, page in manifest["routes"].items():
        for occurrence in page["grafana"]:
            conflicts = _grafana_evidence_conflicts(
                occurrence["sources"],
                occurrence["source_roles"],
                occurrence["source_status"],
            )
            missing_roles = []
            if not occurrence["live_url"]:
                missing_roles.append("live")
            if not occurrence["fallback_url"]:
                missing_roles.append("fallback")
            if conflicts:
                findings.append(
                    {
                        "code": "grafana-source-conflict",
                        "route": route,
                        "location": occurrence["location"],
                        "conflicts": conflicts,
                        "source_digests": occurrence["source_digests"],
                    }
                )
            for role in missing_roles:
                findings.append(
                    {
                        "code": f"grafana-{role}-missing",
                        "route": route,
                        "location": occurrence["location"],
                        "source_digests": occurrence["source_digests"],
                    }
                )
            for source_attribute, role in occurrence["source_roles"].items():
                if role != "fallback" or occurrence["source_status"][source_attribute] != "release-image":
                    continue
                fallback_parts = urlsplit(occurrence["sources"][source_attribute])
                expected_mime = IMAGE_MIME_BY_SUFFIX[Path(fallback_parts.path).suffix.lower()]
                asset = manifest["assets"].get(fallback_parts.path)
                reason = ""
                if not asset or not asset["exists"]:
                    reason = "missing"
                elif asset["decoded_media_type"] != expected_mime:
                    reason = "invalid-image"
                if reason:
                    findings.append(
                        {
                            "code": "grafana-fallback-invalid",
                            "location": occurrence["location"],
                            "path": fallback_parts.path,
                            "reason": reason,
                            "reference_digest": occurrence["source_digests"][source_attribute],
                            "route": route,
                            "source_attribute": source_attribute,
                        }
                    )
    return sorted(findings, key=canonical_json)


def _validate_source_snapshot(value: Any, *, label: str, path: str = "source_snapshot") -> dict[str, Any]:
    snapshot = _exact_object(
        value,
        {
            "activation_eligible",
            "attestation_contract",
            "attestation_schema_version",
            "attestation_sha256",
            "evidence_status",
            "manifest_sha256",
        },
        label=label,
        path=path,
    )
    if snapshot["attestation_contract"] != SNAPSHOT_ATTESTATION_CONTRACT:
        _manifest_schema_error(label, f"{path}.attestation_contract", "uses an unsupported attestation contract")
    if snapshot["attestation_schema_version"] != SNAPSHOT_ATTESTATION_SCHEMA_VERSION:
        _manifest_schema_error(label, f"{path}.attestation_schema_version", "uses an unsupported schema version")
    for key in ("attestation_sha256", "manifest_sha256"):
        value = _string(snapshot[key], label=label, path=f"{path}.{key}", nonempty=True)
        if SHA256_RE.fullmatch(value) is None:
            _manifest_schema_error(label, f"{path}.{key}", "must be lowercase sha256:<64 hex>")
    if snapshot["evidence_status"] != "provisional-only":
        _manifest_schema_error(label, f"{path}.evidence_status", "is not trusted by the supported v1 resolver")
    if snapshot["activation_eligible"] is not False:
        _manifest_schema_error(label, f"{path}.activation_eligible", "cannot be true for the supported v1 attestation")
    return snapshot


def _validate_manifest(manifest: dict[str, Any], label: str) -> None:
    try:
        _json_shape(
            manifest,
            maximum_depth=DEFAULT_LIMITS["manifest_depth"],
            maximum_nodes=MANIFEST_INPUT_MAX_NODES,
        )
    except ValueError as exc:
        raise ValueError(f"{label} manifest exceeds structural limits: {exc}") from exc
    top_keys = {
        "aliases",
        "assets",
        "contract",
        "features",
        "hls",
        "integrity",
        "limits",
        "origin",
        "routes",
        "schema_version",
        "source_snapshot",
        "verification_scope",
    }
    manifest = _exact_object(manifest, top_keys, label=label, path="root")
    if manifest["contract"] != CONTRACT or type(manifest["schema_version"]) is not int:
        raise ValueError(f"{label} is not a {CONTRACT} schema v{SCHEMA_VERSION} manifest")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"{label} is not a {CONTRACT} schema v{SCHEMA_VERSION} manifest")
    origin = _string(manifest["origin"], label=label, path="origin", nonempty=True)
    try:
        normalized_origin = normalize_origin(origin)
    except ValueError as exc:
        _manifest_schema_error(label, "origin", str(exc))
    if normalized_origin != origin:
        _manifest_schema_error(label, "origin", "must be the exact normalized origin")
    source_snapshot = _validate_source_snapshot(manifest["source_snapshot"], label=label)
    limits = _exact_object(manifest["limits"], set(DEFAULT_LIMITS), label=label, path="limits")
    for name, value in limits.items():
        _integer(value, label=label, path=f"limits.{name}", minimum=1)
    expected_scope = _verification_scope_for(source_snapshot)
    if canonical_json(manifest["verification_scope"]) != canonical_json(expected_scope):
        _manifest_schema_error(label, "verification_scope", "must exactly match the derived schema v2 policy")

    routes = manifest["routes"]
    if not isinstance(routes, dict):
        _manifest_schema_error(label, "routes", "must be a JSON object")
    page_keys = {
        "downloads",
        "form_controls",
        "fragment_targets",
        "grafana",
        "headings",
        "links",
        "media",
        "metadata",
        "native_visibility",
        "redirects",
        "revealable",
        "runtime",
        "source",
        "tables",
        "text",
    }
    for route, page in routes.items():
        if not isinstance(route, str) or route != normalize_route(route):
            _manifest_schema_error(label, "routes", f"contains invalid route {route!r}")
        route_path = f"routes.{route}"
        page = _exact_object(page, page_keys, label=label, path=route_path)
        _string(page["source"], label=label, path=f"{route_path}.source", nonempty=True)
        _string(page["text"], label=label, path=f"{route_path}.text")
        _string_list(page["fragment_targets"], label=label, path=f"{route_path}.fragment_targets")
        _validate_metadata(page["metadata"], label=label, path=f"{route_path}.metadata")
        _validate_page_collections(page, label=label, route_path=route_path)
        _validate_grafana(
            page,
            label=label,
            route=route,
            route_path=route_path,
            origin=manifest["origin"],
        )
        _validate_native_visibility(page, label=label, route_path=route_path)

    aliases = manifest["aliases"]
    if not isinstance(aliases, dict):
        _manifest_schema_error(label, "aliases", "must be a JSON object")
    for route, alias in aliases.items():
        if not isinstance(route, str) or route != normalize_route(route):
            _manifest_schema_error(label, "aliases", f"contains invalid route {route!r}")
        alias_path = f"aliases.{route}"
        alias = _exact_object(
            alias,
            {
                "canonical",
                "follow",
                "noindex",
                "refresh_delay",
                "refreshes",
                "robots",
                "robots_by_crawler",
                "source",
                "target",
            },
            label=label,
            path=alias_path,
        )
        _string(alias["source"], label=label, path=f"{alias_path}.source", nonempty=True)
        _string(alias["target"], label=label, path=f"{alias_path}.target", nonempty=True)
        _string(alias["canonical"], label=label, path=f"{alias_path}.canonical")
        _string(alias["refresh_delay"], label=label, path=f"{alias_path}.refresh_delay", nonempty=True)
        if not isinstance(alias["refreshes"], list) or not alias["refreshes"]:
            _manifest_schema_error(label, f"{alias_path}.refreshes", "must be a nonempty JSON array")
        for index, refresh in enumerate(alias["refreshes"]):
            refresh_path = f"{alias_path}.refreshes[{index}]"
            refresh = _exact_object(refresh, {"delay", "target"}, label=label, path=refresh_path)
            _string(refresh["delay"], label=label, path=f"{refresh_path}.delay", nonempty=True)
            _string(refresh["target"], label=label, path=f"{refresh_path}.target", nonempty=True)
        _string_list(alias["robots"], label=label, path=f"{alias_path}.robots")
        _string_list_map(
            alias["robots_by_crawler"],
            label=label,
            path=f"{alias_path}.robots_by_crawler",
            allowed_keys=ROBOT_META_NAMES,
        )
        for alias_field in ("follow", "noindex"):
            if type(alias[alias_field]) is not bool:
                _manifest_schema_error(label, f"{alias_path}.{alias_field}", "must be a JSON boolean")

    assets = manifest["assets"]
    if not isinstance(assets, dict):
        _manifest_schema_error(label, "assets", "must be a JSON object")
    for asset_path, asset in assets.items():
        if not isinstance(asset_path, str) or not asset_path.startswith("/"):
            _manifest_schema_error(label, "assets", f"contains malformed path {asset_path!r}")
        path = f"assets.{asset_path}"
        asset = _exact_object(
            asset,
            {"decoded_media_type", "exists", "references", "sha256", "size", "webvtt_header"},
            label=label,
            path=path,
        )
        decoded_media_type = _string(asset["decoded_media_type"], label=label, path=f"{path}.decoded_media_type")
        expected_media_type = {
            **IMAGE_MIME_BY_SUFFIX,
            **HLS_MIME_BY_SUFFIX,
        }.get(Path(urlsplit(asset_path).path).suffix.lower())
        if decoded_media_type and decoded_media_type != expected_media_type:
            _manifest_schema_error(
                label,
                f"{path}.decoded_media_type",
                "must match the asset extension's supported decoded MIME",
            )
        if type(asset["exists"]) is not bool:
            _manifest_schema_error(label, f"{path}.exists", "must be a JSON boolean")
        if type(asset["webvtt_header"]) is not bool:
            _manifest_schema_error(label, f"{path}.webvtt_header", "must be a JSON boolean")
        if asset["webvtt_header"] and (
            Path(urlsplit(asset_path).path).suffix.lower() != ".vtt" or decoded_media_type != "text/vtt"
        ):
            _manifest_schema_error(
                label,
                f"{path}.webvtt_header",
                "may be true only for a decoded WebVTT asset",
            )
        if asset["exists"]:
            _integer(asset["size"], label=label, path=f"{path}.size", minimum=0)
            if not isinstance(asset["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]):
                _manifest_schema_error(label, f"{path}.sha256", "must be a lowercase SHA-256 hex digest")
        elif asset["size"] is not None or asset["sha256"] is not None or decoded_media_type or asset["webvtt_header"]:
            _manifest_schema_error(label, path, "must use null size and sha256 when exists is false")
        references = asset["references"]
        if not isinstance(references, list):
            _manifest_schema_error(label, f"{path}.references", "must be a JSON array")
        for index, reference in enumerate(references):
            reference_path = f"{path}.references[{index}]"
            reference = _exact_object(reference, {"kind", "route"}, label=label, path=reference_path)
            kind = _string(reference["kind"], label=label, path=f"{reference_path}.kind", nonempty=True)
            if kind not in ASSET_REFERENCE_KINDS:
                _manifest_schema_error(
                    label,
                    f"{reference_path}.kind",
                    f"must be one of {sorted(ASSET_REFERENCE_KINDS)}",
                )
            reference_route = _string(reference["route"], label=label, path=f"{reference_path}.route")
            if reference_route and reference_route not in routes:
                _manifest_schema_error(label, f"{reference_path}.route", "must name a manifest route or be empty")

    for route, page in routes.items():
        for index, runtime_reference in enumerate(page["runtime"]):
            runtime_path = f"routes.{route}.runtime[{index}]"
            href_parts = urlsplit(runtime_reference["href"])
            if href_parts.scheme or href_parts.netloc or not href_parts.path:
                _manifest_schema_error(label, f"{runtime_path}.href", "must be a local release asset")
            asset = assets.get(href_parts.path)
            if not asset or not any(
                reference["kind"] == runtime_reference["kind"] and reference["route"] == route
                for reference in asset["references"]
            ):
                _manifest_schema_error(
                    label,
                    runtime_path,
                    "must have an exactly typed route-scoped asset reference",
                )
            if runtime_reference["rel"] == "preload":
                expected_kind = PRELOAD_AS_KINDS.get(runtime_reference["as"])
                if expected_kind is None and Path(href_parts.path).suffix.lower() == ".wasm":
                    expected_kind = "preload-wasm"
                if runtime_reference["kind"] != expected_kind:
                    _manifest_schema_error(
                        label,
                        f"{runtime_path}.kind",
                        "must be recomputed from the preload as/type target",
                    )
            elif runtime_reference["rel"] == "modulepreload" and runtime_reference["kind"] != "modulepreload":
                _manifest_schema_error(label, f"{runtime_path}.kind", "must be modulepreload")
            elif runtime_reference["rel"] == "stylesheet" and runtime_reference["kind"] != "stylesheet":
                _manifest_schema_error(label, f"{runtime_path}.kind", "must be stylesheet")
            elif runtime_reference["rel"] == "script":
                expected_kind = "module" if runtime_reference["type"] == "module" else "script"
                if runtime_reference["kind"] != expected_kind:
                    _manifest_schema_error(label, f"{runtime_path}.kind", "must match the script type")
            if runtime_reference["kind"] == "preload-image":
                for srcset_entry in runtime_reference["imagesrcset"].split(","):
                    srcset_source = srcset_entry.strip().partition(" ")[0]
                    if not srcset_source:
                        continue
                    srcset_parts = urlsplit(srcset_source)
                    if srcset_parts.scheme or srcset_parts.netloc or not srcset_parts.path:
                        continue
                    if Path(srcset_parts.path).suffix.lower() not in IMAGE_MIME_BY_SUFFIX:
                        continue
                    srcset_asset = assets.get(srcset_parts.path)
                    if not srcset_asset or not any(
                        reference["kind"] == "preload-image" and reference["route"] == route
                        for reference in srcset_asset["references"]
                    ):
                        _manifest_schema_error(
                            label,
                            f"{runtime_path}.imagesrcset",
                            f"lacks typed asset evidence for {srcset_parts.path}",
                        )

    _validate_features(manifest, label=label)
    _validate_hls(manifest, label=label)

    integrity = _exact_object(manifest["integrity"], {"findings", "missing_assets"}, label=label, path="integrity")
    missing_assets = _string_list(integrity["missing_assets"], label=label, path="integrity.missing_assets")
    expected_missing_assets = sorted(path for path, asset in assets.items() if not asset["exists"])
    if missing_assets != expected_missing_assets:
        _manifest_schema_error(
            label,
            "integrity.missing_assets",
            "must exactly equal the assets whose exists field is false",
        )
    findings = integrity["findings"]
    if not isinstance(findings, list):
        _manifest_schema_error(label, "integrity.findings", "must be a JSON array")
    for index, finding in enumerate(findings):
        _validate_integrity_finding(finding, label=label, path=f"integrity.findings[{index}]")

    expected_hls_url_findings = _hls_url_findings_from_manifest(manifest)
    actual_hls_url_findings = [finding for finding in findings if finding["code"] == "hls-unsafe-url"]
    if sorted(actual_hls_url_findings, key=canonical_json) != expected_hls_url_findings:
        _manifest_schema_error(
            label,
            "hls.playlists",
            "does not exactly account for every forbidden HLS URL",
        )

    hls_media_paths = {
        path
        for path, asset in assets.items()
        if any(reference["kind"] == "hls-media" for reference in asset["references"])
    }
    expected_hls_graph_all_findings = _hls_graph_findings(
        manifest["hls"]["playlists"],
        manifest["hls"]["roots"],
        hls_media_paths,
        limits=manifest["limits"],
    )
    graph_codes = {"hls-cycle", "hls-orphan"}
    actual_hls_graph_findings = [finding for finding in findings if finding["code"] in graph_codes]
    expected_hls_graph_findings = [
        finding for finding in expected_hls_graph_all_findings if finding["code"] in graph_codes
    ]
    if sorted(actual_hls_graph_findings, key=canonical_json) != expected_hls_graph_findings:
        _manifest_schema_error(label, "hls", "does not exactly account for graph cycles and orphans")
    expected_hls_graph_limit_findings = [
        finding
        for finding in expected_hls_graph_all_findings
        if finding["code"] == "limit-exceeded"
        and (finding["detail"].startswith("graph depth") or finding["detail"] == "HLS cycle evidence limit exceeded")
    ]
    actual_hls_graph_limit_findings = [
        finding
        for finding in findings
        if finding["code"] == "limit-exceeded"
        and finding["resource"] == "hls"
        and (finding["detail"].startswith("graph depth") or finding["detail"] == "HLS cycle evidence limit exceeded")
    ]
    if sorted(actual_hls_graph_limit_findings, key=canonical_json) != sorted(
        expected_hls_graph_limit_findings,
        key=canonical_json,
    ):
        _manifest_schema_error(label, "hls", "does not exactly account for graph limits")
    expected_target_findings = sorted(
        [
            *_hls_target_type_findings(manifest["hls"]["playlists"]),
            *_hls_payload_findings_from_manifest(manifest),
        ],
        key=canonical_json,
    )
    actual_target_findings = [finding for finding in findings if finding["code"] == "hls-target-type-mismatch"]
    if sorted(actual_target_findings, key=canonical_json) != expected_target_findings:
        _manifest_schema_error(label, "hls", "does not exactly account for dependency target types")

    expected_runtime_findings = _runtime_integrity_findings(routes)
    actual_runtime_findings = [finding for finding in findings if finding["code"] == "invalid-runtime-reference"]
    if sorted(actual_runtime_findings, key=canonical_json) != expected_runtime_findings:
        _manifest_schema_error(label, "routes", "does not exactly account for invalid runtime image references")

    expected_grafana_findings = _grafana_findings_from_manifest(manifest)
    recomputable_grafana_codes = {
        "grafana-fallback-invalid",
        "grafana-fallback-missing",
        "grafana-source-conflict",
    }
    actual_grafana_findings = [finding for finding in findings if finding["code"] in recomputable_grafana_codes]
    expected_grafana_findings = [
        finding for finding in expected_grafana_findings if finding["code"] in recomputable_grafana_codes
    ]
    if sorted(actual_grafana_findings, key=canonical_json) != expected_grafana_findings:
        _manifest_schema_error(
            label,
            "routes",
            "does not exactly account for every Grafana role, fallback, and source conflict",
        )

    expected_alias_findings = _alias_integrity_findings(routes, aliases)
    actual_alias_findings = [finding for finding in findings if finding["code"] == "invalid-alias"]
    if sorted(actual_alias_findings, key=canonical_json) != expected_alias_findings:
        _manifest_schema_error(
            label,
            "aliases",
            "does not exactly account for canonical, robots, target, self/dangling, and cycle integrity",
        )

    canonical_indexable_routes = _canonical_indexable_routes(routes, aliases)
    rss_documents = manifest["features"]["rss"]["evidence"]["documents"]
    expected_rss_findings = _rss_route_findings(rss_documents, canonical_indexable_routes)
    actual_rss_findings = [finding for finding in findings if finding["code"] == "rss-route-mismatch"]
    if sorted(actual_rss_findings, key=canonical_json) != sorted(expected_rss_findings, key=canonical_json):
        _manifest_schema_error(
            label,
            "features.rss.evidence.documents",
            "contains URLs not exactly accounted for by same-origin canonical indexable-route findings",
        )
    sitemap_documents = manifest["features"]["sitemap"]["evidence"]["documents"]
    robots_documents = manifest["features"]["robots"]["evidence"]["documents"]
    _reachable_sitemaps, expected_sitemap_graph_findings = _sitemap_closure(
        sitemap_documents,
        robots_documents,
        limits=manifest["limits"],
    )
    sitemap_graph_codes = {"sitemap-child-invalid", "sitemap-cycle", "sitemap-orphan"}
    actual_sitemap_graph_findings = [finding for finding in findings if finding["code"] in sitemap_graph_codes]
    expected_sitemap_graph_findings = [
        finding for finding in expected_sitemap_graph_findings if finding["code"] in sitemap_graph_codes
    ]
    if sorted(actual_sitemap_graph_findings, key=canonical_json) != sorted(
        expected_sitemap_graph_findings, key=canonical_json
    ):
        _manifest_schema_error(
            label,
            "features.sitemap.evidence.documents",
            "does not exactly account for the local acyclic sitemap closure",
        )
    _reachable_sitemaps, expected_sitemap_all_findings = _sitemap_closure(
        sitemap_documents,
        robots_documents,
        limits=manifest["limits"],
    )
    expected_sitemap_limit_findings = [
        finding
        for finding in expected_sitemap_all_findings
        if finding["code"] == "limit-exceeded"
        and (
            finding["detail"].startswith("sitemap graph")
            or finding["detail"] == "sitemap cycle evidence limit exceeded"
        )
    ]
    actual_sitemap_limit_findings = [
        finding
        for finding in findings
        if finding["code"] == "limit-exceeded"
        and finding["resource"] == "xml"
        and (
            finding["detail"].startswith("sitemap graph")
            or finding["detail"] == "sitemap cycle evidence limit exceeded"
        )
    ]
    if sorted(actual_sitemap_limit_findings, key=canonical_json) != sorted(
        expected_sitemap_limit_findings, key=canonical_json
    ):
        _manifest_schema_error(
            label,
            "features.sitemap.evidence.documents",
            "does not exactly account for sitemap graph limits",
        )
    expected_sitemap_findings = _sitemap_route_findings(
        sitemap_documents,
        canonical_indexable_routes,
        robots_documents,
        limits=manifest["limits"],
    )
    actual_sitemap_findings = [finding for finding in findings if finding["code"] == "sitemap-route-mismatch"]
    if sorted(actual_sitemap_findings, key=canonical_json) != sorted(expected_sitemap_findings, key=canonical_json):
        _manifest_schema_error(
            label,
            "features.sitemap.evidence.documents",
            "does not exactly match the canonical indexable route set",
        )
    expected_robots_findings = _robots_sitemap_findings(robots_documents, sitemap_documents)
    actual_robots_findings = [finding for finding in findings if finding["code"] == "robots-sitemap-mismatch"]
    if sorted(actual_robots_findings, key=canonical_json) != sorted(expected_robots_findings, key=canonical_json):
        _manifest_schema_error(
            label,
            "features.robots.evidence.documents",
            "does not exactly account for referenced sitemap documents",
        )


def _failure_digest(failure: dict[str, Any]) -> str:
    return canonical_digest({key: value for key, value in failure.items() if key != "failure_digest"})


def _exception_code_prohibited(code: str) -> bool:
    return code.startswith(("baseline-", "candidate-")) or "duplicate" in code or "unsafe-tree" in code


def _validate_exceptions_document(
    document: dict[str, Any], *, baseline_digest: str, today: date | None = None
) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        raise ValueError("exceptions file must contain a JSON object")
    expected_top_keys = {"baseline_manifest_digest", "contract", "exceptions", "schema_version"}
    actual_top_keys = set(document)
    if actual_top_keys != expected_top_keys:
        unknown = sorted(actual_top_keys - expected_top_keys)
        missing = sorted(expected_top_keys - actual_top_keys)
        raise ValueError(f"exceptions schema keys mismatch: unknown={unknown}, missing={missing}")
    if (
        document["contract"] != EXCEPTIONS_CONTRACT
        or type(document["schema_version"]) is not int
        or document["schema_version"] != EXCEPTIONS_SCHEMA_VERSION
    ):
        raise ValueError(f"exceptions file must be {EXCEPTIONS_CONTRACT} schema v{EXCEPTIONS_SCHEMA_VERSION}")
    if not isinstance(document["baseline_manifest_digest"], str) or not SHA256_RE.fullmatch(
        document["baseline_manifest_digest"]
    ):
        raise ValueError("exceptions baseline_manifest_digest must be lowercase sha256:<64 hex>")
    if document["baseline_manifest_digest"] != baseline_digest:
        raise ValueError("exceptions file is stale: baseline_manifest_digest does not match the current baseline")
    entries = document["exceptions"]
    if not isinstance(entries, list):
        raise ValueError("exceptions must be a JSON array")

    current_date = today or datetime.now(UTC).date()
    base_keys = {
        "activation_attestation",
        "activation_issue",
        "category",
        "code",
        "expires",
        "failure_digest",
        "owner",
        "reason",
    }
    selector_keys = {"feature", "field", "path", "route"}
    allowed_keys = base_keys | selector_keys
    identities: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(entries):
        if not isinstance(raw_entry, dict):
            raise ValueError(f"exception {index} must be a JSON object")
        unknown = sorted(set(raw_entry) - allowed_keys)
        if unknown:
            raise ValueError(f"exception {index} has unknown keys: {unknown}")
        missing = sorted(base_keys - set(raw_entry))
        if missing:
            raise ValueError(f"exception {index} is missing keys: {missing}")
        selectors = {key: raw_entry[key] for key in sorted(selector_keys & set(raw_entry))}
        if not selectors:
            raise ValueError(f"exception {index} must name route, field, path, or feature as applicable")

        for key in ("code", *selectors):
            value = raw_entry[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"exception {index} {key} must be a nonempty string")
            if WILDCARD_RE.search(value):
                raise ValueError(f"exception {index} {key} contains a forbidden wildcard")
        for key in ("activation_issue", "owner", "reason"):
            value = raw_entry[key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"exception {index} {key} must be a nonempty string")
        if WILDCARD_RE.search(raw_entry["activation_issue"]) or WILDCARD_RE.search(raw_entry["owner"]):
            raise ValueError(f"exception {index} owner/activation_issue contains a forbidden wildcard")
        if not ACTIVATION_ISSUE_RE.fullmatch(raw_entry["activation_issue"]):
            raise ValueError(f"exception {index} activation_issue must identify a GitHub issue")
        if raw_entry["owner"].strip().lower() in {"none", "nobody", "tbd", "team", "unknown", "unassigned"}:
            raise ValueError(f"exception {index} owner must identify a concrete accountable owner")
        if raw_entry["category"] not in EXCEPTION_CATEGORIES:
            raise ValueError(f"exception {index} category must be one of {sorted(EXCEPTION_CATEGORIES)}")
        if raw_entry["activation_attestation"] is not True:
            raise ValueError(f"exception {index} activation_attestation must be the JSON boolean true")
        value = raw_entry["failure_digest"]
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            raise ValueError(f"exception {index} failure_digest must be lowercase sha256:<64 hex>")
        if _exception_code_prohibited(raw_entry["code"]):
            raise ValueError(f"exception {index} code {raw_entry['code']!r} is an unwaivable integrity failure")
        try:
            expires = date.fromisoformat(raw_entry["expires"])
        except (TypeError, ValueError):
            raise ValueError(f"exception {index} expires must be an ISO YYYY-MM-DD date")
        if expires < current_date:
            raise ValueError(f"exception {index} expired on {expires.isoformat()}")

        identity = canonical_json({"code": raw_entry["code"], **selectors})
        if identity in identities:
            raise ValueError(f"duplicate exception identity at entry {index}: {identity}")
        identities.add(identity)
        validated.append(dict(raw_entry))
    return validated


def _apply_exceptions(
    failures: list[dict[str, Any]],
    document: dict[str, Any] | None,
    *,
    baseline_digest: str,
    today: date | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if document is None:
        return failures, []
    entries = _validate_exceptions_document(document, baseline_digest=baseline_digest, today=today)
    applied: list[dict[str, Any]] = []
    applied_digests: set[str] = set()
    selector_keys = {"feature", "field", "path", "route"}
    for index, entry in enumerate(entries):
        entry_selectors = {key: entry[key] for key in selector_keys if key in entry}
        matches = []
        for failure in failures:
            failure_selectors = {key: failure[key] for key in selector_keys if key in failure}
            if failure["code"] == entry["code"] and failure_selectors == entry_selectors:
                matches.append(failure)
        if len(matches) != 1:
            raise ValueError(
                f"exception {index} is stale or unmatched: code={entry['code']!r}, selectors={entry_selectors!r}"
            )
        failure = matches[0]
        if failure["failure_digest"] in applied_digests:
            raise ValueError(f"exception {index} duplicates an already-applied failure")
        if entry["failure_digest"] != failure["failure_digest"]:
            raise ValueError(f"exception {index} has a stale full failure_digest")
        applied_digests.add(failure["failure_digest"])
        applied.append(
            {
                "activation_issue": entry["activation_issue"],
                "activation_attestation": True,
                "category": entry["category"],
                "code": entry["code"],
                "expires": entry["expires"],
                "expected_baseline": failure["expected_baseline"],
                "failure_digest": failure["failure_digest"],
                "owner": entry["owner"],
                "reason": entry["reason"],
                "selectors": entry_selectors,
            }
        )
    remaining = [failure for failure in failures if failure["failure_digest"] not in applied_digests]
    return remaining, sorted(applied, key=canonical_json)


def compare_manifests(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    trusted_source_snapshot: dict[str, Any],
    exceptions: dict[str, Any] | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Compare a required baseline with a candidate; additions do not fail."""
    _validate_manifest(baseline, "baseline")
    _validate_manifest(candidate, "candidate")
    trusted_source_snapshot = _validate_source_snapshot(
        trusted_source_snapshot,
        label="trusted source snapshot",
    )
    failures: list[dict[str, Any]] = []
    additions: dict[str, Any] = {
        "routes": sorted(set(candidate["routes"]) - set(baseline["routes"])),
        "aliases": sorted(set(candidate["aliases"]) - set(baseline["aliases"])),
        "features": [],
        "hls": sorted(set(candidate["hls"]["playlists"]) - set(baseline["hls"]["playlists"])),
        "content": {},
        "assets": sorted(set(candidate["assets"]) - set(baseline["assets"])),
    }

    def fail(code: str, *, expected_baseline: Any, **details: Any) -> None:
        failures.append({"code": code, "expected_baseline": expected_baseline, **details})

    for label, manifest in (("baseline", baseline), ("candidate", candidate)):
        if manifest["source_snapshot"] != trusted_source_snapshot:
            fail(
                f"{label}-source-snapshot-mismatch",
                expected=trusted_source_snapshot,
                actual=manifest["source_snapshot"],
                expected_baseline=trusted_source_snapshot,
            )
    if candidate["origin"] != baseline["origin"]:
        fail(
            "origin-mismatch",
            expected=baseline["origin"],
            actual=candidate["origin"],
            expected_baseline=baseline["origin"],
        )
    if candidate["limits"] != baseline["limits"]:
        fail(
            "limits-mismatch",
            expected=baseline["limits"],
            actual=candidate["limits"],
            expected_baseline=baseline["limits"],
        )
    if candidate["hls"]["roots"] != baseline["hls"]["roots"]:
        fail(
            "hls-roots-mismatch",
            expected=baseline["hls"]["roots"],
            actual=candidate["hls"]["roots"],
            expected_baseline=baseline["hls"]["roots"],
        )

    for route in sorted(set(baseline["routes"]) - set(candidate["routes"])):
        fail("route-missing", route=route, expected_baseline=canonical_digest(baseline["routes"][route]))

    for alias, baseline_alias in baseline["aliases"].items():
        candidate_alias = candidate["aliases"].get(alias)
        if candidate_alias is None:
            fail(
                "alias-missing",
                route=alias,
                expected_target=baseline_alias["target"],
                expected_baseline=baseline_alias["target"],
            )
        elif candidate_alias.get("target") != baseline_alias.get("target"):
            fail(
                "alias-target-mismatch",
                route=alias,
                expected=baseline_alias.get("target"),
                actual=candidate_alias.get("target"),
                expected_baseline=baseline_alias.get("target"),
            )
        else:
            for field in (
                "canonical",
                "follow",
                "noindex",
                "refresh_delay",
                "refreshes",
                "robots_by_crawler",
                "source",
            ):
                if candidate_alias[field] != baseline_alias[field]:
                    fail(
                        "alias-metadata-mismatch",
                        route=alias,
                        field=field,
                        expected=baseline_alias[field],
                        actual=candidate_alias[field],
                        expected_baseline=baseline_alias[field],
                    )

    for path, baseline_playlist in baseline["hls"]["playlists"].items():
        candidate_playlist = candidate["hls"]["playlists"].get(path)
        if candidate_playlist is None:
            fail("hls-playlist-missing", path=path, expected_baseline=canonical_digest(baseline_playlist))
        elif candidate_playlist != baseline_playlist:
            fail(
                "hls-playlist-mismatch",
                path=path,
                expected=baseline_playlist,
                actual=candidate_playlist,
                expected_baseline=baseline_playlist,
            )

    required_metadata = ("title", "description", "canonical", "lang", "base_href")
    collection_fields = (
        ("fragment_targets", "fragment-target-missing"),
        ("headings", "heading-missing"),
        ("tables", "table-missing"),
        ("links", "link-missing"),
        ("downloads", "download-missing"),
        ("media", "media-missing"),
        ("grafana", "grafana-missing"),
        ("form_controls", "form-control-missing"),
        ("redirects", "redirect-missing"),
        ("revealable", "revealable-content-missing"),
    )
    for route in sorted(set(baseline["routes"]) & set(candidate["routes"])):
        baseline_page = baseline["routes"][route]
        candidate_page = candidate["routes"][route]
        page_additions: dict[str, Any] = {}

        baseline_tokens = semantic_tokens(baseline_page.get("text", ""))
        candidate_tokens = semantic_tokens(candidate_page.get("text", ""))
        missing_tokens, additional_tokens = _subsequence_delta(baseline_tokens, candidate_tokens)
        if missing_tokens:
            fail(
                "content-missing",
                route=route,
                missing_token_count=len(missing_tokens),
                missing_sample=missing_tokens[:20],
                expected_baseline=canonical_digest(baseline_page.get("text", "")),
            )
        if additional_tokens:
            page_additions["text"] = {"token_count": len(additional_tokens), "sample": additional_tokens[:20]}

        for collection_field, code in collection_fields:
            baseline_items = _comparison_items(collection_field, baseline_page.get(collection_field, []))
            candidate_items = _comparison_items(collection_field, candidate_page.get(collection_field, []))
            if collection_field in {"form_controls", "native_visibility", "revealable"}:
                baseline_items = [item for item in baseline_items if item.get("location") == "content"]
                candidate_items = [item for item in candidate_items if item.get("location") == "content"]
            missing = (
                _missing_links(baseline_items, candidate_items)
                if collection_field == "links"
                else _missing_multiset(baseline_items, candidate_items)
            )
            if missing:
                fail(code, route=route, missing=missing, expected_baseline=missing)
            additional = _additional_multiset(baseline_items, candidate_items)
            if additional:
                page_additions[collection_field] = additional

        baseline_meta = baseline_page.get("metadata", {})
        candidate_meta = candidate_page.get("metadata", {})
        for metadata_field in required_metadata:
            baseline_value = baseline_meta.get(metadata_field)
            candidate_value = candidate_meta.get(metadata_field)
            if metadata_field == "description":
                baseline_value = meaningful_metadata_text(baseline_value)
                candidate_value = meaningful_metadata_text(candidate_value)
            if (
                baseline_value
                and baseline_value != candidate_value
                and not _is_not_found_metadata_upgrade(route, metadata_field, baseline_value, candidate_value)
            ):
                fail(
                    "metadata-mismatch",
                    route=route,
                    field=metadata_field,
                    expected=baseline_value,
                    actual=candidate_value,
                    expected_baseline=baseline_value,
                )
            elif not baseline_value and candidate_value:
                page_additions.setdefault("metadata", {})[metadata_field] = candidate_value
        if baseline_meta.get("noindex", False) != candidate_meta.get(
            "noindex", False
        ) and not _is_not_found_metadata_upgrade(
            route,
            "noindex",
            baseline_meta.get("noindex", False),
            candidate_meta.get("noindex", False),
        ):
            fail(
                "metadata-mismatch",
                route=route,
                field="noindex",
                expected=baseline_meta.get("noindex", False),
                actual=candidate_meta.get("noindex", False),
                expected_baseline=baseline_meta.get("noindex", False),
            )
        for group in ("open_graph", "twitter"):
            for metadata_field, baseline_value in baseline_meta.get(group, {}).items():
                candidate_value = candidate_meta.get(group, {}).get(metadata_field, "")
                if metadata_field == "description":
                    baseline_value = meaningful_metadata_text(baseline_value)
                    candidate_value = meaningful_metadata_text(candidate_value)
                if baseline_value and candidate_value != baseline_value:
                    fail(
                        "metadata-mismatch",
                        route=route,
                        field=f"{group}.{metadata_field}",
                        expected=baseline_value,
                        actual=candidate_value,
                        expected_baseline=baseline_value,
                    )
                elif not baseline_value and candidate_value:
                    page_additions.setdefault("metadata", {})[f"{group}.{metadata_field}"] = candidate_value
        for robots_field in ("robots_by_crawler",):
            baseline_robots = baseline_meta.get(robots_field, [] if robots_field == "robots" else {})
            candidate_robots = candidate_meta.get(robots_field, [] if robots_field == "robots" else {})
            if candidate_robots != baseline_robots:
                fail(
                    "metadata-mismatch",
                    route=route,
                    field=robots_field,
                    expected=baseline_robots,
                    actual=candidate_robots,
                    expected_baseline=baseline_robots,
                )
        baseline_nofollow = "nofollow" in baseline_meta.get("robots", [])
        candidate_nofollow = "nofollow" in candidate_meta.get("robots", [])
        if candidate_nofollow != baseline_nofollow:
            fail(
                "metadata-mismatch",
                route=route,
                field="nofollow",
                expected=baseline_nofollow,
                actual=candidate_nofollow,
                expected_baseline=baseline_nofollow,
            )
        if candidate_meta.get("refreshes", []) != baseline_meta.get("refreshes", []):
            fail(
                "metadata-mismatch",
                route=route,
                field="refreshes",
                expected=baseline_meta.get("refreshes", []),
                actual=candidate_meta.get("refreshes", []),
                expected_baseline=baseline_meta.get("refreshes", []),
            )

        if page_additions:
            additions["content"][route] = page_additions

    for feature in FEATURE_NAMES:
        baseline_present = bool(baseline["features"].get(feature, {}).get("present"))
        candidate_present = bool(candidate["features"].get(feature, {}).get("present"))
        if baseline_present and not candidate_present:
            fail("feature-missing", feature=feature, expected_baseline=True)
        elif candidate_present and not baseline_present:
            additions["features"].append(feature)
        if baseline_present and feature in {"downloads", "katex"}:
            baseline_route_evidence = baseline["features"].get(feature, {}).get("evidence", {}).get("routes", {})
            candidate_route_evidence = candidate["features"].get(feature, {}).get("evidence", {}).get("routes", {})
            for route, baseline_evidence in sorted(baseline_route_evidence.items()):
                candidate_evidence = candidate_route_evidence.get(route)
                if candidate_evidence is None:
                    fail(
                        "feature-route-missing",
                        feature=feature,
                        route=route,
                        expected_baseline=canonical_digest(baseline_evidence),
                    )
                    continue
                missing_evidence = _missing_multiset(baseline_evidence, candidate_evidence)
                if missing_evidence:
                    fail(
                        "feature-route-evidence-missing",
                        feature=feature,
                        route=route,
                        missing=missing_evidence,
                        expected_baseline=missing_evidence,
                    )

    for feature in ("rss", "sitemap"):
        baseline_documents = {
            document["path"]: document
            for document in baseline["features"].get(feature, {}).get("evidence", {}).get("documents", [])
        }
        candidate_documents = {
            document["path"]: document
            for document in candidate["features"].get(feature, {}).get("evidence", {}).get("documents", [])
        }
        for path, baseline_document in sorted(baseline_documents.items()):
            candidate_document = candidate_documents.get(path)
            if candidate_document is None:
                fail(
                    f"{feature}-document-missing",
                    path=path,
                    expected_baseline=canonical_digest(baseline_document),
                )
                continue
            if candidate_document.get("root") != baseline_document.get("root"):
                fail(
                    f"{feature}-document-mismatch",
                    path=path,
                    field="root",
                    expected=baseline_document.get("root"),
                    actual=candidate_document.get("root"),
                    expected_baseline=baseline_document.get("root"),
                )
            if feature == "rss" and candidate_document.get("metadata") != baseline_document.get("metadata"):
                baseline_metadata = baseline_document.get("metadata", {})
                candidate_metadata = candidate_document.get("metadata", {})
                metadata_fields = sorted(
                    key
                    for key in set(baseline_metadata) | set(candidate_metadata)
                    if baseline_metadata.get(key) != candidate_metadata.get(key)
                )
                fail(
                    "rss-metadata-mismatch",
                    path=path,
                    fields=metadata_fields,
                    expected=baseline_metadata,
                    actual=candidate_metadata,
                    expected_baseline=baseline_metadata,
                )
            missing_entries = _missing_multiset(
                baseline_document.get("entries", []), candidate_document.get("entries", [])
            )
            if missing_entries:
                fail(
                    f"{feature}-entry-missing",
                    path=path,
                    missing=missing_entries,
                    expected_baseline=missing_entries,
                )

    baseline_robots_documents = {
        document["path"]: document for document in baseline["features"]["robots"]["evidence"]["documents"]
    }
    candidate_robots_documents = {
        document["path"]: document for document in candidate["features"]["robots"]["evidence"]["documents"]
    }
    for path, baseline_document in baseline_robots_documents.items():
        candidate_document = candidate_robots_documents.get(path)
        if candidate_document is None:
            fail(
                "robots-document-missing",
                path=path,
                expected_baseline=canonical_digest(baseline_document),
            )
        elif candidate_document != baseline_document:
            fail(
                "robots-document-mismatch",
                path=path,
                expected=baseline_document,
                actual=candidate_document,
                expected_baseline=baseline_document,
            )

    for label, manifest in (("baseline", baseline), ("candidate", candidate)):
        for path in manifest.get("integrity", {}).get("missing_assets", []):
            baseline_asset = baseline.get("assets", {}).get(path)
            fail(
                f"{label}-asset-missing",
                path=path,
                expected_baseline=baseline_asset.get("sha256") if baseline_asset else None,
            )
        for finding in manifest.get("integrity", {}).get("findings", []):
            details = {key: value for key, value in finding.items() if key != "code"}
            fail(
                f"{label}-{finding['code']}",
                expected_baseline=None if label == "candidate" else finding,
                **details,
            )

    for path in sorted(_semantic_asset_paths(baseline)):
        baseline_asset = baseline["assets"].get(path)
        candidate_asset = candidate["assets"].get(path)
        if candidate_asset is None:
            fail(
                "asset-missing",
                path=path,
                expected_baseline=baseline_asset.get("sha256") if baseline_asset else None,
            )
            continue
        if not candidate_asset.get("exists"):
            fail(
                "candidate-asset-missing",
                path=path,
                expected_baseline=baseline_asset.get("sha256") if baseline_asset else None,
            )
            continue
        if baseline_asset and (
            baseline_asset.get("size") != candidate_asset.get("size")
            or baseline_asset.get("sha256") != candidate_asset.get("sha256")
        ):
            fail(
                "asset-mismatch",
                path=path,
                expected_size=baseline_asset.get("size"),
                actual_size=candidate_asset.get("size"),
                expected_sha256=baseline_asset.get("sha256"),
                actual_sha256=candidate_asset.get("sha256"),
                expected_baseline=baseline_asset.get("sha256"),
            )

    failures = [item for _key, item in sorted({canonical_json(item): item for item in failures}.items())]
    for failure in failures:
        failure["failure_digest"] = _failure_digest(failure)
    failures, applied_exceptions = _apply_exceptions(
        failures,
        exceptions,
        baseline_digest=canonical_digest(baseline),
        today=today,
    )
    additions["features"].sort()
    additions["content"] = dict(sorted(additions["content"].items()))
    baseline_boundary = baseline["verification_scope"]["snapshot_boundary"]
    candidate_boundary = candidate["verification_scope"]["snapshot_boundary"]
    activation_eligible = bool(
        trusted_source_snapshot["activation_eligible"]
        and baseline["source_snapshot"] == trusted_source_snapshot
        and candidate["source_snapshot"] == trusted_source_snapshot
        and baseline_boundary["activation_eligible"]
        and candidate_boundary["activation_eligible"]
    )
    activation = {
        "status": "activation-eligible" if activation_eligible else "activation-blocked-provisional-evidence",
        "activation_eligible": activation_eligible,
        "baseline_local_evidence_status": baseline_boundary["local_evidence_status"],
        "candidate_local_evidence_status": candidate_boundary["local_evidence_status"],
        "mandatory_activation_boundary": baseline_boundary["mandatory_activation_boundary"],
        "source_snapshot_revalidated": True,
        "exception_waivable": False,
    }
    return {
        "contract": CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "source_snapshot": _json_deep_copy(trusted_source_snapshot),
        "baseline_manifest_digest": canonical_digest(baseline),
        "compatible": not failures,
        "activation": activation,
        "failures": failures,
        "applied_exceptions": applied_exceptions,
        "additions": additions,
        "comparison_policy": _verification_scope_for(trusted_source_snapshot),
        "exception_policy": {
            "contract": EXCEPTIONS_CONTRACT,
            "schema_version": EXCEPTIONS_SCHEMA_VERSION,
            "allowed_categories": sorted(EXCEPTION_CATEGORIES),
            "integrity_failures_waivable": False,
        },
        "structural_evidence": {
            "baseline": _json_deep_copy(baseline["features"]),
            "candidate": _json_deep_copy(candidate["features"]),
        },
        "summary": {
            "baseline_routes": len(baseline["routes"]),
            "candidate_routes": len(candidate["routes"]),
            "baseline_aliases": len(baseline["aliases"]),
            "candidate_aliases": len(candidate["aliases"]),
            "failure_count": len(failures),
            "applied_exception_count": len(applied_exceptions),
            "raw_failure_count": len(failures) + len(applied_exceptions),
        },
    }


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _strict_json_bytes(value: bytes, *, label: str, maximum_nodes: int) -> dict[str, Any]:
    try:
        document = json.loads(
            value.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
        _json_shape(
            document,
            maximum_depth=DEFAULT_LIMITS["manifest_depth"],
            maximum_nodes=maximum_nodes,
        )
    except (
        MemoryError,
        OverflowError,
        RecursionError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        detail = str(exc) or "bounded JSON parse exhausted memory"
        raise ValueError(f"{label} is invalid: {detail}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return document


def _verify_source_snapshot_content(root: Path, expected_files: dict[str, str]) -> None:
    """Bind a content manifest to one stable, bounded, no-follow tree scan."""

    content_root = root / "content"
    limits = dict(DEFAULT_LIMITS)
    try:
        with StageSnapshotLease(content_root) as lease:
            lease.verify()
            verified_root, paths, findings, tree_snapshot = _tree_preflight(content_root, limits)
            if findings or tree_snapshot is None:
                code = findings[0]["code"] if findings else "unsafe-tree-read-error"
                raise ValueError(f"source snapshot content tree is unsafe: {code}")

            actual_files = {path.relative_to(verified_root).as_posix(): path for path in paths}
            expected_paths = set(expected_files)
            actual_paths = set(actual_files)
            if actual_paths != expected_paths:
                missing = sorted(expected_paths - actual_paths)[:5]
                extra = sorted(actual_paths - expected_paths)[:5]
                raise ValueError(
                    "source snapshot content tree membership does not match its manifest "
                    f"(missing={missing}, extra={extra})"
                )

            verifier = StageBoundaryVerifier(verified_root, tree_snapshot, limits)
            for relative in sorted(expected_files):
                _size, digest = verifier.sha256_file(actual_files[relative], limits["asset_bytes"])
                if digest != expected_files[relative]:
                    raise ValueError(f"source snapshot content digest mismatch: {relative}")
            boundary_findings = verifier.verify()
            if boundary_findings:
                raise ValueError(
                    f"source snapshot content tree changed during verification: {boundary_findings[0]['code']}"
                )
            lease.verify()
    except SafeFileError as exc:
        raise ValueError(f"source snapshot content tree is unsafe: {exc.code}") from exc


def read_source_snapshot(root: Path) -> dict[str, Any]:
    """Resolve the currently trusted provisional snapshot into a closed identity."""

    attestation_bytes = _bounded_bytes(root / "attestation.json", SNAPSHOT_ATTESTATION_MAX_BYTES)
    manifest_bytes = _bounded_bytes(root / "manifests" / "content.json", SNAPSHOT_MANIFEST_MAX_BYTES)
    attestation = _strict_json_bytes(
        attestation_bytes,
        label="source snapshot attestation",
        maximum_nodes=1_000,
    )
    content_manifest = _strict_json_bytes(
        manifest_bytes,
        label="source snapshot content manifest",
        maximum_nodes=SNAPSHOT_MANIFEST_MAX_FILES * 3,
    )

    attestation_keys = {
        "activationEligible",
        "contract",
        "evidenceStatus",
        "guardFindings",
        "guardReportSha256",
        "guardSchemaVersion",
        "policyVersion",
        "sanitizedFileCount",
        "sanitizedManifestSha256",
        "schemaVersion",
        "sourceFileCount",
        "sourceManifestSha256",
        "transformations",
    }
    if set(attestation) != attestation_keys:
        raise ValueError("source snapshot attestation does not use the closed v1 shape")
    canonical_attestation = json.dumps(attestation, ensure_ascii=False, indent=2) + "\n"
    if canonical_attestation.encode("utf-8") != attestation_bytes:
        raise ValueError("source snapshot attestation is not canonical JSON")
    if (
        attestation["contract"] != SNAPSHOT_ATTESTATION_CONTRACT
        or attestation["schemaVersion"] != SNAPSHOT_ATTESTATION_SCHEMA_VERSION
        or attestation["evidenceStatus"] != "provisional-only"
        or attestation["activationEligible"] is not False
    ):
        raise ValueError("source snapshot attestation is not trusted by the supported v1 resolver")

    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    if (
        not isinstance(attestation["sanitizedManifestSha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", attestation["sanitizedManifestSha256"]) is None
        or attestation["sanitizedManifestSha256"] != manifest_digest
    ):
        raise ValueError("source snapshot attestation does not bind the supplied content manifest")
    if (
        set(content_manifest) != {"files", "version"}
        or content_manifest["version"] != 1
        or not isinstance(content_manifest["files"], dict)
    ):
        raise ValueError("source snapshot content manifest does not use the closed v1 shape")
    files = content_manifest["files"]
    if len(files) > SNAPSHOT_MANIFEST_MAX_FILES:
        raise ValueError("source snapshot content manifest exceeds the file-count limit")
    if type(attestation["sanitizedFileCount"]) is not int or attestation["sanitizedFileCount"] != len(files):
        raise ValueError("source snapshot attestation file count does not match its content manifest")
    if type(attestation["sourceFileCount"]) is not int or attestation["sourceFileCount"] < len(files):
        raise ValueError("source snapshot attestation source file count is invalid")
    for relative, digest in files.items():
        relative_parts = relative.split("/") if isinstance(relative, str) else []
        if (
            not isinstance(relative, str)
            or not relative
            or len(relative.encode("utf-8")) > DEFAULT_LIMITS["path_bytes"]
            or "\x00" in relative
            or "\\" in relative
            or relative.startswith("/")
            or any(part in {"", ".", ".."} for part in relative_parts)
            or posixpath.normpath(relative) != relative
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ValueError("source snapshot content manifest contains an invalid file record")
    for key in ("guardReportSha256", "sourceManifestSha256"):
        if not isinstance(attestation[key], str) or re.fullmatch(r"[0-9a-f]{64}", attestation[key]) is None:
            raise ValueError(f"source snapshot attestation {key} is invalid")
    if (
        not isinstance(attestation["policyVersion"], str)
        or not attestation["policyVersion"]
        or type(attestation["guardSchemaVersion"]) is not int
        or attestation["guardSchemaVersion"] < 1
        or attestation["guardFindings"] != 0
    ):
        raise ValueError("source snapshot attestation public-output policy evidence is invalid")
    transformations = attestation["transformations"]
    transformation_keys = {
        "changedFiles",
        "hlsFilesPreserved",
        "invalidValueRepairFiles",
        "pngReencodeFiles",
        "textRedactionFiles",
    }
    if (
        not isinstance(transformations, dict)
        or set(transformations) != transformation_keys
        or any(type(value) is not int or value < 0 for value in transformations.values())
    ):
        raise ValueError("source snapshot attestation transformation evidence is invalid")

    _verify_source_snapshot_content(root, files)
    if (
        _bounded_bytes(root / "attestation.json", SNAPSHOT_ATTESTATION_MAX_BYTES) != attestation_bytes
        or _bounded_bytes(root / "manifests" / "content.json", SNAPSHOT_MANIFEST_MAX_BYTES) != manifest_bytes
    ):
        raise ValueError("source snapshot metadata changed during content verification")

    return {
        "attestation_contract": attestation["contract"],
        "attestation_schema_version": attestation["schemaVersion"],
        "attestation_sha256": f"sha256:{hashlib.sha256(attestation_bytes).hexdigest()}",
        "manifest_sha256": f"sha256:{manifest_digest}",
        "evidence_status": attestation["evidenceStatus"],
        "activation_eligible": False,
    }


def read_manifest(
    path: Path,
    *,
    maximum_bytes: int = DEFAULT_LIMITS["manifest_bytes"],
    maximum_nodes: int | None = None,
) -> dict[str, Any]:
    maximum_nodes = MANIFEST_INPUT_MAX_NODES if maximum_nodes is None else maximum_nodes
    if type(maximum_nodes) is not int or maximum_nodes < 1:
        raise ValueError("manifest node limit must be a positive integer")
    try:
        value = json.loads(
            _bounded_bytes(path, maximum_bytes).decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
        _json_shape(
            value,
            maximum_depth=DEFAULT_LIMITS["manifest_depth"],
            maximum_nodes=maximum_nodes,
        )
    except (
        MemoryError,
        OSError,
        OverflowError,
        RecursionError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        detail = str(exc) or "bounded manifest parse exhausted memory"
        raise ValueError(f"cannot read manifest {path}: {detail}")
    if not isinstance(value, dict):
        raise ValueError(f"manifest {path} must contain a JSON object")
    return value


def write_json(value: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        sys.stdout.write(rendered)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest_parser = subparsers.add_parser("manifest", help="inventory a static build")
    manifest_parser.add_argument("build", type=Path, help="static build directory")
    manifest_parser.add_argument("-o", "--output", type=Path, help="write JSON here instead of stdout")
    manifest_parser.add_argument("--origin", default=DEFAULT_ORIGIN, help="canonical site origin")
    manifest_parser.add_argument(
        "--snapshot-root",
        required=True,
        type=Path,
        help="verified source snapshot containing attestation.json and manifests/content.json",
    )
    manifest_parser.add_argument(
        "--limit",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="override one strict parser/resource limit; may be repeated",
    )

    compare_parser = subparsers.add_parser(
        "compare",
        help="compare baseline and candidate manifests",
        description=(
            "Compare structural evidence. Compatible provisional evidence exits 3 by default; "
            "--allow-provisional permits diagnostic exit 0 without conferring activation."
        ),
    )
    compare_parser.add_argument("baseline", type=Path)
    compare_parser.add_argument("candidate", type=Path)
    compare_parser.add_argument(
        "--snapshot-root",
        required=True,
        type=Path,
        help="revalidate the exact source snapshot recorded by both manifests",
    )
    compare_parser.add_argument(
        "--exceptions",
        type=Path,
        help="strict v2 JSON exceptions bound to this baseline and each full current failure digest",
    )
    compare_parser.add_argument(
        "--allow-provisional",
        action="store_true",
        help="allow diagnostic exit 0 for compatible provisional evidence; does not confer activation",
    )
    compare_parser.add_argument("-o", "--output", type=Path, help="write JSON here instead of stdout")
    return parser


def _parse_limit_overrides(values: list[str]) -> dict[str, int] | None:
    if not values:
        return None
    overrides: dict[str, int] = {}
    for value in values:
        name, separator, raw_number = value.partition("=")
        if not separator or name in overrides:
            raise ValueError(f"limit override must be a unique NAME=VALUE pair: {value!r}")
        try:
            overrides[name] = int(raw_number)
        except ValueError as exc:
            raise ValueError(f"limit {name!r} must be an integer") from exc
    return overrides


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "manifest":
            source_snapshot = read_source_snapshot(args.snapshot_root)
            manifest = build_manifest(
                args.build,
                source_snapshot=source_snapshot,
                origin=args.origin,
                limits=_parse_limit_overrides(args.limit),
            )
            write_json(manifest, args.output)
            return 1 if manifest["integrity"]["missing_assets"] or manifest["integrity"]["findings"] else 0
        exception_document = read_manifest(args.exceptions) if args.exceptions else None
        report = compare_manifests(
            read_manifest(args.baseline),
            read_manifest(args.candidate),
            trusted_source_snapshot=read_source_snapshot(args.snapshot_root),
            exceptions=exception_document,
        )
        write_json(report, args.output)
        if not report["compatible"]:
            return 1
        if report["activation"]["activation_eligible"] or args.allow_provisional:
            return 0
        return 3
    except (MemoryError, OSError, OverflowError, RecursionError, UnicodeError, ValueError) as exc:
        detail = str(exc) or "bounded parity operation failed"
        print(f"error: {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
