#!/usr/bin/env python3
"""Audit lab.verdify.ai source content, build output, and Grafana embeds.

This is a validation gate for the Quartz website. It intentionally checks the
source vault and live Grafana, because Quartz builds can succeed while iframe
panel IDs are stale.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

DEFAULT_VAULT = Path("/mnt/iris/verdify-vault/website")
DEFAULT_PUBLIC = Path("/srv/verdify/verdify-site/public")
DEFAULT_IMAGE_MANIFEST = Path("docs/site-image-manifest.json")
DEFAULT_GRAFANA_CONTAINER = "verdify-grafana"
DEFAULT_SITE_CONTAINER = "verdify-site"
DEFAULT_LAUNCH_LINT = Path("scripts/lint_public_site.py")
REBUILD_RETRY_ATTEMPTS = 25
REBUILD_RETRY_SLEEP_SEC = 2
BOX_DRAWING_RE = re.compile(r"[│┌└├─═╔]")
DENVER_TZ = ZoneInfo("America/Denver")
FORECAST_MAX_AGE_SECONDS = 2 * 60 * 60
HOURLY_PERFORMANCE_MAX_AGE_SECONDS = 36 * 60 * 60
STATIC_SNAPSHOT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
PLAN_INDEX_ROW_RE = re.compile(r"^\| \[(\d{4}-\d{2}-\d{2})\]\(/plans/\1\)")
PLAN_PREVIOUS_HYPOTHESIS_RE = re.compile(r"^\*\*Previous hypothesis:\*\*\s*(.+)$", re.MULTILINE)
PLAN_RESULT_RE = re.compile(r"^(?:>\s*)?\*\*Result:\*\*\s*(.+)$", re.MULTILINE)
NAV_BLOCK_RE = re.compile(
    r"<nav\b[^>]*class=[\"'][^\"']*\bsite-nav\b[^\"']*[\"'][^>]*>.*?</nav>",
    re.IGNORECASE | re.DOTALL,
)
HREF_RE = re.compile(r"<a\b[^>]*\bhref=[\"']([^\"']+)[\"']", re.IGNORECASE)
STATIC_SNAPSHOT_RE = re.compile(
    r"(?:Static public API snapshot|Snapshot from the live database).*?\*\*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) MDT\*\*",
    re.IGNORECASE,
)
LONG_PARAGRAPH_MIN_CHARS = 180
LONG_SENTENCE_MIN_CHARS = 140
CANONICAL_FACT_OWNERS: tuple[tuple[str, re.Pattern[str], set[str]], ...] = (
    (
        "greenhouse-footprint",
        re.compile(r"\b367\s+(?:sq\s*ft|square feet)\b", re.IGNORECASE),
        {"greenhouse/structure.md"},
    ),
    (
        "greenhouse-elevation",
        re.compile(r"\b(?:5,090|5090|5,000|5000|4,979|4979)\s*(?:ft|feet)\b", re.IGNORECASE),
        {"greenhouse/structure.md"},
    ),
    (
        "relay-loop-cadence",
        re.compile(r"(?<!\d)5[- ]second|every\s+5\s+seconds", re.IGNORECASE),
        {"reference/safety.md"},
    ),
)

GENERATED_PAGES = {
    "data/baseline-vs-iris.md": "scripts/generate-baseline-vs-iris-page.py",
    "data/forecast/index.md": "scripts/generate-forecast-page.py",
    "data/hourly-performance.md": "scripts/export-hourly-performance-dataset.py",
    "data/plans/index.md": "scripts/generate-plans-index.py",
    "reference/ai-tunables.md": "scripts/generate-ai-tunables-page.py",
    "plans/index.md": "scripts/generate-plans-index.py",
    "reference/lessons.md": "scripts/generate-lessons-page.py",
}
GENERATED_PREFIXES = {
    "plans/": "scripts/generate-daily-plan.py",
    "greenhouse/zones/": "scripts/render-zone-pages.py",
    "greenhouse/crops/": "scripts/render-crop-profiles.py",
}
GENERATED_EXCEPTIONS = {
    "greenhouse/zones/index.md",
    "greenhouse/zones/center.md",
    "greenhouse/crops/index.md",
}
GENERATED_PARTIALS = {
    "greenhouse/equipment.md": "scripts/render-equipment-page.py",
}
GENERATED_ROUTE_ALIASES = (
    (
        "plans/index.md",
        "data/plans/index.md",
        "scripts/generate-plans-index.py",
    ),
)
RETIRED_SOURCE_PATHS = (
    "ai-greenhouse.md",
    "climate/index.md",
    "contact.md",
    "data/slack-ops",
    "evidence/economics.md",
    "evidence/index.md",
    "evidence/operations.md",
    "evidence/planning-quality.md",
    "forecast/index.md",
    "greenhouse/cameras.md",
    "greenhouse/operations.md",
    "greenhouse/lessons.md",
    "greenhouse/lessons/raw.md",
    "intelligence",
    "reference/faq.md",
    "reference/inference.md",
    "reference/openclaw.md",
    "start/slack-ops/index.md",
    "slack",
)
RETIRED_EMPTY_DIRS = ("press", "project", "updates")
UNIFIED_PUBLISHER = Path("scripts/publish-site-content.sh")


@dataclass(frozen=True)
class IframeRef:
    file: str
    line: int
    src: str
    dashboard_uid: str
    panel_id: str


@dataclass(frozen=True)
class ImageRef:
    file: str
    line: int
    ref: str


@dataclass(frozen=True)
class LinkRef:
    file: str
    line: int
    ref: str


@dataclass(frozen=True)
class SemanticIframe:
    file: str
    line: int
    heading: str
    dashboard_uid: str
    panel_id: str
    panel_title: str


@dataclass
class PageInfo:
    path: str
    title: str = ""
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    iframe_count: int = 0
    image_count: int = 0
    link_count: int = 0
    generated_by: str | None = None
    generated_marker: bool = False


@dataclass
class Finding:
    severity: str
    code: str
    message: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 4 :]
    return parse_simple_yaml(raw), body


def parse_simple_yaml(raw: str) -> dict[str, object]:
    """Parse the small frontmatter subset needed for inventory."""
    data: dict[str, object] = {}
    current_key: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")) and current_key:
            value = line.strip()
            if value.startswith("- "):
                existing = data.setdefault(current_key, [])
                if isinstance(existing, list):
                    existing.append(value[2:].strip().strip('"'))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if value.startswith("[") and value.endswith("]"):
            data[key] = [item.strip().strip('"') for item in value[1:-1].split(",") if item.strip()]
        elif value:
            data[key] = value.strip().strip('"')
        else:
            data[key] = []
    return data


def is_draft_page(path: Path) -> bool:
    fm, _body = frontmatter(read_text(path))
    return str(fm.get("draft", "")).strip().lower() == "true"


def is_noindex_page(path: Path) -> bool:
    fm, _body = frontmatter(read_text(path))
    return str(fm.get("noindex", "")).strip().lower() == "true"


def generated_source(rel_path: str) -> str | None:
    if rel_path in GENERATED_PAGES:
        return GENERATED_PAGES[rel_path]
    if rel_path in GENERATED_PARTIALS:
        return GENERATED_PARTIALS[rel_path]
    if rel_path in GENERATED_EXCEPTIONS:
        return None
    for prefix, source in GENERATED_PREFIXES.items():
        if rel_path.startswith(prefix) and re.search(r"/\d{4}-\d{2}-\d{2}\.md$|/[^/]+\.md$", f"/{rel_path}"):
            return source
    return None


def has_generated_marker(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "auto-generated",
        "generated by",
        "do not edit",
        "do not edit by hand",
        "rendered from db",
        "source of truth",
        "regenerate",
        "auto-render",
    )
    return any(marker in lowered for marker in markers)


def extract_iframes(rel_path: str, text: str) -> list[IframeRef]:
    refs: list[IframeRef] = []
    for match in re.finditer(r"<iframe\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", text, flags=re.IGNORECASE):
        src = match.group(1)
        parsed = urlparse(src)
        if parsed.netloc != "graphs.verdify.ai":
            continue
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2 or parts[0] != "d-solo":
            continue
        panel_id = parse_qs(parsed.query).get("panelId", [""])[0]
        refs.append(
            IframeRef(
                file=rel_path,
                line=line_number(text, match.start()),
                src=src,
                dashboard_uid=parts[1],
                panel_id=panel_id,
            )
        )
    return refs


def extract_images(rel_path: str, text: str) -> list[ImageRef]:
    refs: list[ImageRef] = []
    patterns = [
        r"!\[[^\]]*\]\(([^)]+)\)",
        r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>",
        r"<meta\b[^>]*\b(?:property|name)=[\"'](?:og:image|twitter:image)[\"'][^>]*\bcontent=[\"']([^\"']+)[\"']",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            ref = match.group(1).strip()
            if ref.startswith(("http://", "https://", "data:", "#", "mailto:")):
                continue
            refs.append(ImageRef(file=rel_path, line=line_number(text, match.start()), ref=ref))
    return refs


def extract_links(rel_path: str, text: str) -> list[LinkRef]:
    refs: list[LinkRef] = []
    patterns = [
        r"(?<!!)\[[^\]]+\]\(([^)]+)\)",
        r"<a\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*>",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            ref = match.group(1).strip()
            if should_skip_link(ref):
                continue
            refs.append(LinkRef(file=rel_path, line=line_number(text, match.start()), ref=ref))

    for match in re.finditer(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]", text):
        ref = match.group(1).strip()
        if should_skip_link(ref):
            continue
        refs.append(LinkRef(file=rel_path, line=line_number(text, match.start()), ref=ref))
    return refs


def should_skip_link(ref: str) -> bool:
    return not ref or ref.startswith(("#", "http://", "https://", "mailto:", "tel:", "data:")) or ref.startswith("{")


def resolve_site_asset(vault_root: Path, rel_file: str, ref: str) -> Path:
    clean = ref.split("?", 1)[0].split("#", 1)[0]
    if clean.startswith("/"):
        return vault_root / clean.lstrip("/")
    return vault_root / rel_file / ".." / clean


def collect_pages(
    vault_root: Path,
) -> tuple[list[PageInfo], list[IframeRef], list[ImageRef], list[LinkRef], list[Finding]]:
    pages: list[PageInfo] = []
    iframes: list[IframeRef] = []
    images: list[ImageRef] = []
    links: list[LinkRef] = []
    findings: list[Finding] = []

    for path in sorted(vault_root.rglob("*.md")):
        rel_path = path.relative_to(vault_root).as_posix()
        text = read_text(path)
        replacement_count = text.count("\ufffd")
        if replacement_count:
            findings.append(
                Finding(
                    severity="warn",
                    code="replacement-character",
                    message=f"{rel_path} contains {replacement_count} Unicode replacement characters",
                )
            )
        fm, _body = frontmatter(text)
        page_iframes = extract_iframes(rel_path, text)
        page_images = extract_images(rel_path, text)
        page_links = extract_links(rel_path, text)
        source = generated_source(rel_path)
        marker = has_generated_marker(text)
        title = str(fm.get("title") or "")
        tags = fm.get("tags") if isinstance(fm.get("tags"), list) else []
        aliases = fm.get("aliases") if isinstance(fm.get("aliases"), list) else []
        canonical_aliases = canonical_paths_for_page(rel_path)
        for alias in aliases:
            normalized_alias = normalize_route(str(alias))
            if normalized_alias in canonical_aliases:
                findings.append(
                    Finding(
                        severity="error",
                        code="self-alias",
                        message=f"{rel_path} aliases its own canonical route ({alias}); Quartz may emit a redirect stub",
                    )
                )
        if "```mermaid" in text:
            findings.append(
                Finding(
                    severity="error",
                    code="raw-mermaid",
                    message=f"{rel_path} contains a raw Mermaid block; use web-friendly HTML components instead",
                )
            )
        if BOX_DRAWING_RE.search(text):
            findings.append(
                Finding(
                    severity="error",
                    code="ascii-diagram",
                    message=f"{rel_path} contains box-drawing ASCII; use flow/floor-plan components instead",
                )
            )
        pages.append(
            PageInfo(
                path=rel_path,
                title=title,
                tags=[str(tag) for tag in tags],
                aliases=[str(alias) for alias in aliases],
                iframe_count=len(page_iframes),
                image_count=len(page_images),
                link_count=len(page_links),
                generated_by=source,
                generated_marker=marker,
            )
        )
        if source and not marker:
            findings.append(
                Finding(
                    severity="warn",
                    code="generated-marker-missing",
                    message=f"{rel_path} is produced by {source} but has no clear generated/source marker",
                )
            )
        iframes.extend(page_iframes)
        images.extend(page_images)
        links.extend(page_links)

    return pages, iframes, images, links, findings


def clean_scalar(value: object) -> str:
    return str(value or "").strip().strip("'\"")


def check_generated_route_aliases(vault_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for left_rel, right_rel, source in GENERATED_ROUTE_ALIASES:
        left = vault_root / left_rel
        right = vault_root / right_rel
        if not left.exists():
            findings.append(
                Finding("error", "generated-alias-missing", f"{source} expected {left_rel}, but it is missing")
            )
            continue
        if not right.exists():
            findings.append(
                Finding("error", "generated-alias-missing", f"{source} expected {right_rel}, but it is missing")
            )
            continue
        left_text = read_text(left)
        right_text = read_text(right)
        if any(PLAN_INDEX_ROW_RE.match(line) for line in left_text.splitlines()):
            findings.append(
                Finding(
                    "error",
                    "generated-alias-duplicates-canonical",
                    f"{left_rel} contains archive rows; {right_rel} should be the only generated index table",
                )
            )
        if "/data/plans/" not in left_text:
            findings.append(
                Finding("error", "generated-alias-target-missing", f"{left_rel} does not point readers to /data/plans/")
            )
        if first_plan_index_date(right) is None:
            findings.append(Finding("error", "generated-canonical-empty", f"{right_rel} has no daily plan rows"))
    return findings


def check_forecast_freshness(vault_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for rel in ("data/forecast/index.md",):
        path = vault_root / rel
        if not path.exists():
            findings.append(Finding("error", "forecast-page-missing", f"{rel} is missing"))
            continue
        fm, _body = frontmatter(read_text(path))
        raw = clean_scalar(fm.get("last_updated"))
        if not raw:
            findings.append(Finding("error", "forecast-freshness-missing", f"{rel} has no last_updated frontmatter"))
            continue
        try:
            updated_at = datetime.fromisoformat(raw)
        except ValueError:
            findings.append(Finding("error", "forecast-freshness-invalid", f"{rel} has invalid last_updated: {raw}"))
            continue
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=DENVER_TZ)
        age_seconds = (datetime.now(updated_at.tzinfo) - updated_at).total_seconds()
        if age_seconds > FORECAST_MAX_AGE_SECONDS:
            findings.append(
                Finding(
                    "error",
                    "forecast-page-stale",
                    f"{rel} last_updated is {int(age_seconds)}s old; verdify-forecast-page.timer should refresh every 30min",
                )
            )
    return findings


def check_hourly_performance_freshness(vault_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    manifest_path = vault_root / "static" / "data" / "hourly-performance" / "manifest.json"
    page_path = vault_root / "data" / "hourly-performance.md"
    if not page_path.exists():
        findings.append(Finding("error", "hourly-performance-page-missing", "data/hourly-performance.md is missing"))
    if not manifest_path.exists():
        findings.append(
            Finding(
                "error",
                "hourly-performance-manifest-missing",
                "static/data/hourly-performance/manifest.json is missing",
            )
        )
        return findings
    try:
        manifest = json.loads(read_text(manifest_path))
    except json.JSONDecodeError as exc:
        findings.append(Finding("error", "hourly-performance-manifest-invalid", f"{manifest_path}: {exc}"))
        return findings
    raw = str(manifest.get("generated_at") or "")
    try:
        generated_at = datetime.fromisoformat(raw)
    except ValueError:
        findings.append(
            Finding("error", "hourly-performance-freshness-invalid", f"manifest generated_at is invalid: {raw}")
        )
        return findings
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=DENVER_TZ)
    age_seconds = (datetime.now(generated_at.tzinfo) - generated_at).total_seconds()
    if age_seconds > HOURLY_PERFORMANCE_MAX_AGE_SECONDS:
        findings.append(
            Finding(
                "error",
                "hourly-performance-stale",
                f"hourly-performance export is {int(age_seconds // 3600)}h old; run scripts/export-hourly-performance-dataset.py",
            )
        )
    current = manifest.get("current_archive") or {}
    for rel_url in (current.get("url"), (manifest.get("latest") or {}).get("url")):
        if not rel_url:
            findings.append(
                Finding("error", "hourly-performance-url-missing", "manifest is missing current/latest URLs")
            )
            continue
        rel_path = str(rel_url).removeprefix("/")
        if not (vault_root / rel_path).exists():
            findings.append(
                Finding("error", "hourly-performance-file-missing", f"{rel_path} referenced by manifest is missing")
            )
    if int(current.get("rows") or 0) < 24:
        findings.append(
            Finding(
                "error",
                "hourly-performance-row-count-low",
                f"manifest current_archive rows is too low: {current.get('rows')}",
            )
        )
    return findings


def check_static_snapshot_freshness(vault_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    now = datetime.now(DENVER_TZ)
    for path in sorted(vault_root.rglob("*.md")):
        rel_path = path.relative_to(vault_root).as_posix()
        if is_plan_archive_path(rel_path):
            continue
        text = read_text(path)
        for match in STATIC_SNAPSHOT_RE.finditer(text):
            raw = match.group(1)
            try:
                snapshot_at = datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=DENVER_TZ)
            except ValueError:
                findings.append(
                    Finding("error", "static-snapshot-invalid", f"{rel_path} has invalid static snapshot time: {raw}")
                )
                continue
            age_seconds = (now - snapshot_at).total_seconds()
            if age_seconds < -60 * 60:
                findings.append(
                    Finding("error", "static-snapshot-future", f"{rel_path} snapshot time is in the future: {raw} MDT")
                )
            elif age_seconds > STATIC_SNAPSHOT_MAX_AGE_SECONDS:
                findings.append(
                    Finding(
                        "error",
                        "static-snapshot-stale",
                        f"{rel_path} static snapshot is {int(age_seconds // 3600)}h old; refresh or remove latest/current wording",
                    )
                )
    return findings


def first_plan_index_date(path: Path) -> str | None:
    if not path.exists():
        return None
    for line in read_text(path).splitlines():
        match = PLAN_INDEX_ROW_RE.match(line)
        if match:
            return match.group(1)
    return None


def check_plan_archive_freshness(vault_root: Path) -> list[Finding]:
    plan_dates = sorted(
        path.stem for path in (vault_root / "plans").glob("*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem)
    )
    if not plan_dates:
        return [Finding("error", "plan-pages-missing", "plans/YYYY-MM-DD.md pages are missing")]

    findings: list[Finding] = []
    latest_plan = plan_dates[-1]
    rel = "data/plans/index.md"
    first_date = first_plan_index_date(vault_root / rel)
    if first_date is None:
        findings.append(Finding("error", "plans-index-empty", f"{rel} has no daily plan rows"))
    elif first_date != latest_plan:
        findings.append(
            Finding(
                "error",
                "plans-index-stale",
                f"{rel} starts at {first_date}, but latest plan page is {latest_plan}; regenerate with scripts/generate-plans-index.py",
            )
        )
    return findings


def normalize_plan_result(value: str) -> str:
    return plain_text_from_html(value)


def plain_text_from_html(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return re.sub(r"\s+", " ", value).strip()


def check_plan_archive_content(vault_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    plan_dir = vault_root / "plans"
    for path in sorted(plan_dir.glob("*.md")):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem):
            continue
        rel_path = path.relative_to(vault_root).as_posix()
        _fm, body = frontmatter(read_text(path))
        if "```json" in body:
            findings.append(
                Finding(
                    "error",
                    "plan-raw-json-block",
                    f"{rel_path} exposes raw structured JSON in the rendered body; regenerate with scripts/generate-daily-plan.py",
                )
            )
        seen_hypotheses: dict[str, int] = {}
        for match in PLAN_PREVIOUS_HYPOTHESIS_RE.finditer(body):
            normalized = normalize_plan_result(match.group(1))
            if len(normalized) < 120 or normalized.startswith("shown earlier for"):
                continue
            first_line = seen_hypotheses.get(normalized)
            if first_line is not None:
                findings.append(
                    Finding(
                        "error",
                        "plan-duplicate-previous-hypothesis",
                        f"{rel_path} repeats the same previous hypothesis at lines {first_line} and {line_number(body, match.start())}",
                    )
                )
            else:
                seen_hypotheses[normalized] = line_number(body, match.start())
        seen_results: dict[str, int] = {}
        for match in PLAN_RESULT_RE.finditer(body):
            normalized = normalize_plan_result(match.group(1))
            if len(normalized) < 120:
                continue
            first_line = seen_results.get(normalized)
            if first_line is not None:
                findings.append(
                    Finding(
                        "error",
                        "plan-duplicate-result",
                        f"{rel_path} repeats the same result at lines {first_line} and {line_number(body, match.start())}",
                    )
                )
            else:
                seen_results[normalized] = line_number(body, match.start())
    return findings


def is_plan_archive_path(rel_path: str) -> bool:
    return rel_path.startswith("plans/") or rel_path.startswith("data/plans/")


def normalized_prose_blocks(text: str) -> tuple[list[str], list[str]]:
    """Return long public-prose paragraphs and sentences for repetition checks."""
    _fm, body = frontmatter(text)
    body = re.sub(r"<iframe\b[^>]*></iframe>", "", body, flags=re.IGNORECASE)
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<style\b.*?</style>", "", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    body = re.sub(r"<[^>]+>", " ", body)

    paragraphs: list[str] = []
    sentences: list[str] = []
    for paragraph in re.split(r"\n\s*\n", body):
        normalized = re.sub(r"\s+", " ", paragraph).strip()
        if not normalized or normalized.startswith("|"):
            continue
        if len(normalized) >= LONG_PARAGRAPH_MIN_CHARS:
            paragraphs.append(normalized.lower())
        for sentence in re.split(r"(?<=[.!?])\s+", normalized):
            normalized_sentence = re.sub(r"\s+", " ", sentence).strip()
            if len(normalized_sentence) >= LONG_SENTENCE_MIN_CHARS and not normalized_sentence.startswith("|"):
                sentences.append(normalized_sentence.lower())
    return paragraphs, sentences


def check_duplicate_panels(iframes: list[IframeRef]) -> list[Finding]:
    owners_by_panel: dict[tuple[str, str], set[str]] = defaultdict(set)
    for iframe in iframes:
        if is_plan_archive_path(iframe.file):
            continue
        owners_by_panel[(iframe.dashboard_uid, iframe.panel_id)].add(iframe.file)

    findings: list[Finding] = []
    for (dashboard_uid, panel_id), owners in sorted(owners_by_panel.items()):
        if len(owners) <= 1:
            continue
        findings.append(
            Finding(
                "error",
                "duplicate-grafana-panel",
                f"{dashboard_uid} panelId={panel_id} appears on multiple non-plan pages: {', '.join(sorted(owners))}",
            )
        )
    return findings


def check_repeated_public_prose(vault_root: Path) -> list[Finding]:
    paragraphs_by_text: dict[str, set[str]] = defaultdict(set)
    sentences_by_text: dict[str, set[str]] = defaultdict(set)
    for path in sorted(vault_root.rglob("*.md")):
        rel_path = path.relative_to(vault_root).as_posix()
        if is_plan_archive_path(rel_path):
            continue
        paragraphs, sentences = normalized_prose_blocks(read_text(path))
        for paragraph in paragraphs:
            paragraphs_by_text[paragraph].add(rel_path)
        for sentence in sentences:
            sentences_by_text[sentence].add(rel_path)

    findings: list[Finding] = []
    for paragraph, owners in sorted(paragraphs_by_text.items()):
        if len(owners) <= 1:
            continue
        preview = paragraph[:120] + ("..." if len(paragraph) > 120 else "")
        findings.append(
            Finding(
                "error",
                "repeated-public-paragraph",
                f"long paragraph is repeated across pages ({', '.join(sorted(owners))}): {preview}",
            )
        )
    for sentence, owners in sorted(sentences_by_text.items()):
        if len(owners) <= 1:
            continue
        preview = sentence[:120] + ("..." if len(sentence) > 120 else "")
        findings.append(
            Finding(
                "error",
                "repeated-public-sentence",
                f"long sentence is repeated across pages ({', '.join(sorted(owners))}): {preview}",
            )
        )
    return findings


def check_canonical_fact_owners(vault_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(vault_root.rglob("*.md")):
        rel_path = path.relative_to(vault_root).as_posix()
        if is_plan_archive_path(rel_path):
            continue
        text = read_text(path)
        for code, pattern, owners in CANONICAL_FACT_OWNERS:
            if rel_path in owners:
                continue
            for match in pattern.finditer(text):
                findings.append(
                    Finding(
                        "error",
                        f"{code}-outside-owner",
                        f"{rel_path}:{line_number(text, match.start())} repeats a canonical fact owned by {', '.join(sorted(owners))}",
                    )
                )
    return findings


def normalize_route(route: str) -> str:
    clean = route.split("?", 1)[0].split("#", 1)[0].strip()
    clean = clean.removeprefix("/")
    clean = clean.removesuffix("/")
    clean = clean.removesuffix(".html")
    clean = clean.removesuffix(".md")
    return clean or "index"


def canonical_paths_for_page(rel_path: str) -> set[str]:
    path = Path(rel_path)
    no_suffix = path.with_suffix("").as_posix()
    paths = {normalize_route(no_suffix)}
    if path.name == "index.md":
        parent = path.parent.as_posix()
        paths.add(normalize_route(parent if parent != "." else "index"))
    return paths


def route_for_source_page(rel_path: str) -> str:
    path = Path(rel_path)
    if path.name == "index.md":
        parent = path.parent.as_posix()
        return normalize_route(parent if parent != "." else "index")
    return normalize_route(path.with_suffix("").as_posix())


def is_navigation_exempt_page(rel_path: str) -> bool:
    return rel_path in {
        "plans/index.md",
        "reference/planner-contract.md",
    }


def check_duplicate_routes(pages: list[PageInfo]) -> list[Finding]:
    owners_by_route: dict[str, set[str]] = defaultdict(set)
    for page in pages:
        for route in canonical_paths_for_page(page.path):
            owners_by_route[route].add(page.path)
        for alias in page.aliases:
            owners_by_route[normalize_route(alias)].add(f"{page.path} alias")

    findings: list[Finding] = []
    for route, owners in sorted(owners_by_route.items()):
        owner_pages = {owner.removesuffix(" alias") for owner in owners}
        if len(owner_pages) > 1:
            findings.append(
                Finding(
                    "error",
                    "duplicate-route",
                    f"{route} is emitted by multiple source pages: {', '.join(sorted(owners))}",
                )
            )
    return findings


def check_retired_source_paths(vault_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for rel in RETIRED_SOURCE_PATHS:
        path = vault_root / rel
        if path.exists():
            findings.append(
                Finding(
                    "error",
                    "retired-site-source",
                    f"{rel} still exists; collapse legacy content into the canonical nav/source path",
                )
            )
    for rel in RETIRED_EMPTY_DIRS:
        path = vault_root / rel
        if path.exists() and path.is_dir() and not any(path.iterdir()):
            findings.append(Finding("error", "retired-empty-dir", f"{rel}/ is an empty retired website directory"))
    return findings


def check_generation_entrypoint(repo_root: Path | None = None) -> list[Finding]:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent
    publisher = repo_root / UNIFIED_PUBLISHER
    if not publisher.exists():
        return [Finding("error", "site-publisher-missing", f"{UNIFIED_PUBLISHER} is missing")]

    findings: list[Finding] = []
    expected_refs = {
        "scripts/publish-daily-plan.sh": "publish-site-content.sh",
        "systemd/verdify-forecast-page.service": "publish-site-content.sh",
        "systemd/verdify-plan-publish.service": "publish-site-content.sh",
    }
    for rel, needle in expected_refs.items():
        path = repo_root / rel
        if not path.exists():
            findings.append(Finding("error", "site-publisher-wrapper-missing", f"{rel} is missing"))
            continue
        if needle not in read_text(path):
            findings.append(
                Finding(
                    "error",
                    "site-publisher-bypass",
                    f"{rel} does not call {UNIFIED_PUBLISHER}; generated content refreshes must use one entry point",
                )
            )
    return findings


def load_image_manifest(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    images = data.get("images", {})
    return images if isinstance(images, dict) else {}


def check_images(
    vault_root: Path, images: list[ImageRef], image_manifest: dict[str, dict[str, object]]
) -> list[Finding]:
    findings: list[Finding] = []
    for image in images:
        resolved = resolve_site_asset(vault_root, image.file, image.ref).resolve()
        if not resolved.exists():
            findings.append(
                Finding(
                    severity="error",
                    code="missing-image",
                    message=f"{image.file}:{image.line} references missing image {image.ref}",
                )
            )
            continue
        if image.ref.startswith("/static/photos/"):
            filename = Path(image.ref).name
            entry = image_manifest.get(filename)
            if entry is None:
                findings.append(
                    Finding(
                        severity="error",
                        code="image-manifest-missing",
                        message=f"{image.file}:{image.line} references {filename}, which is not in docs/site-image-manifest.json",
                    )
                )
                continue
            if entry.get("status") != "approved":
                findings.append(
                    Finding(
                        severity="error",
                        code="image-not-approved",
                        message=f"{image.file}:{image.line} references {filename}, status={entry.get('status')}",
                    )
                )
            allowed_paths = entry.get("allowed_paths")
            if isinstance(allowed_paths, list) and allowed_paths:
                if not any(image.file == str(prefix) or image.file.startswith(str(prefix)) for prefix in allowed_paths):
                    findings.append(
                        Finding(
                            severity="error",
                            code="image-out-of-place",
                            message=f"{image.file}:{image.line} uses {filename} outside allowed paths: {', '.join(str(p) for p in allowed_paths)}",
                        )
                    )
    return findings


def resolve_internal_link(vault_root: Path, link: LinkRef) -> Path | None:
    clean = link.ref.split("?", 1)[0].split("#", 1)[0]
    if clean.endswith("/"):
        clean = clean.rstrip("/")
    if clean in ("", "."):
        return vault_root / "index.md"

    if clean.startswith("/"):
        rel = clean.lstrip("/")
        rel_candidates = [Path(rel)]
    else:
        rel_candidates = [Path(link.file).parent / clean, Path(clean)]

    candidates: list[Path] = []
    for rel_path in rel_candidates:
        if rel_path.suffix:
            candidates.append(vault_root / rel_path)
            if rel_path.suffix == ".html":
                candidates.append((vault_root / rel_path).with_suffix(".md"))
        else:
            candidates.extend(
                [
                    vault_root / rel_path / "index.md",
                    (vault_root / rel_path).with_suffix(".md"),
                ]
            )

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def check_links(vault_root: Path, links: list[LinkRef]) -> list[Finding]:
    findings: list[Finding] = []
    for link in links:
        if resolve_internal_link(vault_root, link) is None:
            findings.append(
                Finding(
                    severity="error",
                    code="missing-internal-link",
                    message=f"{link.file}:{link.line} links to missing page or asset {link.ref}",
                )
            )
    return findings


def docker_curl_json(container: str, path: str, timeout: int = 15) -> dict | list:
    result = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            f"curl -sS --max-time {timeout} http://localhost:3000{path}",
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 5,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"docker exec {container} curl {path} failed: {stderr}")
    return json.loads(result.stdout)


def dashboard_panel_titles(dashboard: dict) -> dict[str, str]:
    titles: dict[str, str] = {}

    def walk(items: list[dict] | None) -> None:
        for item in items or []:
            if item.get("type") == "row":
                walk(item.get("panels"))
            elif "id" in item:
                titles[str(item["id"])] = str(item.get("title") or "")

    walk(dashboard.get("panels"))
    return titles


def check_grafana(
    iframes: list[IframeRef],
    container: str,
) -> tuple[dict[str, set[str]], dict[str, dict[str, str]], list[Finding]]:
    findings: list[Finding] = []
    panel_ids_by_uid: dict[str, set[str]] = {}
    panel_titles_by_uid: dict[str, dict[str, str]] = {}
    for uid in sorted({iframe.dashboard_uid for iframe in iframes}):
        try:
            payload = docker_curl_json(container, f"/api/dashboards/uid/{uid}")
        except Exception as exc:
            findings.append(Finding("error", "grafana-fetch-failed", f"{uid}: {exc}"))
            panel_ids_by_uid[uid] = set()
            panel_titles_by_uid[uid] = {}
            continue
        dashboard = payload.get("dashboard") if isinstance(payload, dict) else None
        if not dashboard:
            findings.append(Finding("error", "grafana-dashboard-missing", f"dashboard UID {uid} not found"))
            panel_ids_by_uid[uid] = set()
            panel_titles_by_uid[uid] = {}
            continue
        panel_titles_by_uid[uid] = dashboard_panel_titles(dashboard)
        panel_ids_by_uid[uid] = set(panel_titles_by_uid[uid])

    for iframe in iframes:
        if not iframe.panel_id:
            findings.append(
                Finding(
                    "error",
                    "grafana-panel-missing",
                    f"{iframe.file}:{iframe.line} iframe has no panelId for dashboard {iframe.dashboard_uid}",
                )
            )
            continue
        if iframe.panel_id not in panel_ids_by_uid.get(iframe.dashboard_uid, set()):
            findings.append(
                Finding(
                    "error",
                    "grafana-panel-stale",
                    f"{iframe.file}:{iframe.line} references {iframe.dashboard_uid} panelId={iframe.panel_id}, which is not present live",
                )
            )
    return panel_ids_by_uid, panel_titles_by_uid, findings


def collect_semantic_iframes(
    vault_root: Path,
    iframes: list[IframeRef],
    panel_titles_by_uid: dict[str, dict[str, str]],
) -> list[SemanticIframe]:
    iframe_files = {iframe.file for iframe in iframes}
    headings_by_file: dict[str, list[tuple[int, str]]] = {}
    for rel_path in iframe_files:
        text = read_text(vault_root / rel_path)
        headings: list[tuple[int, str]] = []
        for offset, line in enumerate(text.splitlines(), start=1):
            match = re.match(r"^(#{1,4})\s+(.+)", line)
            if match:
                headings.append((offset, plain_text_from_html(match.group(2))))
                continue
            html_match = re.match(r"^\s*<h[1-4]\b[^>]*>(.*?)</h[1-4]>\s*$", line, flags=re.IGNORECASE)
            if html_match:
                headings.append((offset, plain_text_from_html(html_match.group(1))))
        headings_by_file[rel_path] = headings

    semantic: list[SemanticIframe] = []
    for iframe in iframes:
        heading = ""
        for line, title in headings_by_file.get(iframe.file, []):
            if line <= iframe.line:
                heading = title
            else:
                break
        semantic.append(
            SemanticIframe(
                file=iframe.file,
                line=iframe.line,
                heading=heading,
                dashboard_uid=iframe.dashboard_uid,
                panel_id=iframe.panel_id,
                panel_title=panel_titles_by_uid.get(iframe.dashboard_uid, {}).get(iframe.panel_id, ""),
            )
        )
    return semantic


def check_public_output(vault_root: Path, public_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for _attempt in range(REBUILD_RETRY_ATTEMPTS):
        if public_root.exists() and (public_root / "index.html").is_file():
            break
        time.sleep(REBUILD_RETRY_SLEEP_SEC)
    if not public_root.exists():
        return [Finding("error", "public-missing", f"public output directory missing: {public_root}")]
    if not (public_root / "index.html").is_file():
        findings.append(Finding("error", "public-index-missing", f"missing built index.html in {public_root}"))

    source_pages = {
        path.relative_to(vault_root).with_suffix(".html").as_posix()
        for path in vault_root.rglob("*.md")
        if not is_draft_page(path)
    }
    missing: list[str] = []
    for _attempt in range(REBUILD_RETRY_ATTEMPTS):
        missing = []
        for rel in sorted(source_pages):
            output = public_root / rel
            if rel == "index.html":
                output = public_root / "index.html"
            if not output.exists():
                missing.append(rel)
        if not missing:
            break
        time.sleep(REBUILD_RETRY_SLEEP_SEC)
    for rel in missing:
        findings.append(Finding("warn", "public-page-missing", f"source page has no built output: {rel}"))
    return findings


def compact_route_list(routes: list[str], limit: int = 20) -> str:
    suffix = f", ... (+{len(routes) - limit} more)" if len(routes) > limit else ""
    return ", ".join(routes[:limit]) + suffix


def expected_navigation_routes(vault_root: Path) -> set[str]:
    return set(source_navigation_routes(vault_root).keys())


def source_navigation_routes(vault_root: Path, *, include_exempt: bool = False) -> dict[str, str]:
    routes: dict[str, str] = {}
    for path in sorted(vault_root.rglob("*.md")):
        if is_draft_page(path):
            continue
        rel_path = path.relative_to(vault_root).as_posix()
        route = route_for_source_page(rel_path)
        if route == "404" or route.startswith("tags/"):
            continue
        if not include_exempt and is_navigation_exempt_page(rel_path):
            continue
        routes[route] = rel_path
    return routes


def base_path_for_route(route: str, route_sources: dict[str, str]) -> str:
    rel_path = route_sources.get(route, "")
    if route == "index":
        return "/"
    if rel_path and Path(rel_path).name == "index.md":
        return f"/{route}/"
    parent = Path(route).parent.as_posix()
    return "/" if parent == "." else f"/{parent}/"


def route_from_href(raw_href: str, base_route: str, route_sources: dict[str, str]) -> str | None:
    href = html.unescape(raw_href).strip()
    if not href or href.startswith(("#", "mailto:", "tel:")):
        return None
    parsed = urlparse(href)
    if parsed.scheme or parsed.netloc:
        return None
    if not parsed.path:
        return None

    if parsed.path.startswith("/"):
        path = parsed.path
    else:
        path = urljoin(base_path_for_route(base_route, route_sources), parsed.path)

    route = normalize_route(path)
    if route.startswith("static/"):
        return None
    return route


def public_paths_for_route(public_root: Path, route: str) -> list[Path]:
    if route == "index":
        return [public_root / "index.html"]
    return [public_root / route / "index.html", public_root / f"{route}.html"]


def extract_internal_routes(
    html_text: str,
    *,
    base_route: str = "index",
    nav_only: bool = False,
    route_sources: dict[str, str] | None = None,
) -> set[str]:
    route_sources = route_sources or {}
    source = html_text
    if nav_only:
        nav_match = NAV_BLOCK_RE.search(html_text)
        if not nav_match:
            return set()
        source = nav_match.group(0)

    routes: set[str] = set()
    for raw_href in HREF_RE.findall(source):
        route = route_from_href(raw_href, base_route, route_sources)
        if route:
            routes.add(route)
    return routes


def extract_navigation_routes(html_text: str) -> set[str]:
    return extract_internal_routes(html_text, nav_only=True)


def linked_routes_from_public_page(public_root: Path, route: str, route_sources: dict[str, str]) -> set[str]:
    for path in public_paths_for_route(public_root, route):
        if path.is_file():
            return extract_internal_routes(read_text(path), base_route=route, route_sources=route_sources)
    return set()


def check_navigation_coverage(vault_root: Path, public_root: Path) -> list[Finding]:
    index_path = public_root / "index.html"
    if not index_path.is_file():
        return [Finding("error", "nav-index-missing", f"cannot inspect site navigation; missing {index_path}")]

    source_routes = source_navigation_routes(vault_root)
    all_source_routes = source_navigation_routes(vault_root, include_exempt=True)
    nav_routes = extract_internal_routes(
        read_text(index_path),
        nav_only=True,
        route_sources=all_source_routes,
    )
    if not nav_routes:
        return [Finding("error", "nav-block-missing", f"no site-nav links found in {index_path}")]

    discoverable_routes = set(nav_routes)
    for route in sorted(nav_routes & set(all_source_routes)):
        discoverable_routes.update(linked_routes_from_public_page(public_root, route, all_source_routes))

    findings: list[Finding] = []
    missing = sorted(set(source_routes) - discoverable_routes)
    if missing:
        findings.append(
            Finding(
                "error",
                "nav-route-missing",
                f"{len(missing)} source route(s) not discoverable from site navigation or linked hub pages: {compact_route_list(missing)}",
            )
        )

    stale = sorted(
        route for route in nav_routes - set(all_source_routes) if route != "index" and not route.startswith("tags/")
    )
    if stale:
        findings.append(
            Finding(
                "error",
                "nav-route-stale",
                f"{len(stale)} internal nav route(s) have no source page: {compact_route_list(stale)}",
            )
        )
    return findings


def check_site_container(container: str) -> list[Finding]:
    result = None
    for _attempt in range(REBUILD_RETRY_ATTEMPTS):
        result = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "sh",
                "-c",
                "test -r /usr/share/nginx/html/index.html && test -r /usr/share/nginx/html/404.html",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            return []
        time.sleep(REBUILD_RETRY_SLEEP_SEC)
    if result.returncode == 0:
        return []
    detail = (result.stderr or result.stdout).strip() or "index/404 unreadable"
    return [Finding("error", "site-container-bind-mount", f"{container} cannot read nginx html bind mount: {detail}")]


def check_launch_lint(vault_root: Path, public_root: Path, skip_public: bool) -> list[Finding]:
    if not DEFAULT_LAUNCH_LINT.exists():
        return [Finding("warn", "launch-lint-missing", f"{DEFAULT_LAUNCH_LINT} is missing")]

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fh:
        report_path = Path(fh.name)
    try:
        cmd = [
            sys.executable,
            str(DEFAULT_LAUNCH_LINT),
            "--vault-root",
            str(vault_root),
            "--public-root",
            str(public_root),
            "--json-report",
            str(report_path),
            "--quiet",
        ]
        if skip_public:
            cmd.append("--skip-public")
        subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        findings = []
        for item in payload.get("findings", []):
            findings.append(
                Finding(
                    severity=str(item.get("severity", "warn")),
                    code=f"launch-{item.get('code', 'lint')}",
                    message=str(item.get("message", "")),
                )
            )
        return findings
    except Exception as exc:
        return [Finding("warn", "launch-lint-failed", f"launch lint failed to run: {exc}")]
    finally:
        try:
            report_path.unlink()
        except FileNotFoundError:
            pass


def print_summary(
    pages: list[PageInfo],
    iframes: list[IframeRef],
    images: list[ImageRef],
    links: list[LinkRef],
    panel_ids_by_uid: dict[str, set[str]],
    findings: list[Finding],
) -> None:
    severities = Counter(f.severity for f in findings)
    generated = [page for page in pages if page.generated_by]
    generated_missing = [page for page in generated if not page.generated_marker]
    print("Verdify site doctor")
    print(f"  pages: {len(pages)}")
    print(f"  generated or partial-generated pages: {len(generated)} ({len(generated_missing)} missing markers)")
    print(f"  grafana iframes: {len(iframes)} across {len({i.file for i in iframes})} pages")
    print(f"  grafana dashboards referenced: {len(panel_ids_by_uid)}")
    print(f"  local image refs: {len(images)}")
    print(f"  internal links: {len(links)}")
    print(f"  findings: {len(findings)} ({severities.get('error', 0)} errors, {severities.get('warn', 0)} warnings)")

    by_dashboard: dict[str, set[str]] = defaultdict(set)
    for iframe in iframes:
        by_dashboard[iframe.dashboard_uid].add(iframe.panel_id)
    if by_dashboard:
        print("\nGrafana embed inventory:")
        for uid in sorted(by_dashboard):
            live_count = len(panel_ids_by_uid.get(uid, set()))
            embedded = sorted(by_dashboard[uid], key=lambda value: int(value) if value.isdigit() else 10**9)
            print(f"  {uid}: {len(embedded)} embedded panel IDs, {live_count} live panels")

    if findings:
        print("\nFindings:")
        severity_order = {"error": 0, "warn": 1, "info": 2}
        for finding in sorted(findings, key=lambda f: (severity_order.get(f.severity, 9), f.code, f.message)):
            print(f"  [{finding.severity}] {finding.code}: {finding.message}")


def write_json_report(
    path: Path,
    pages: list[PageInfo],
    iframes: list[IframeRef],
    semantic_iframes: list[SemanticIframe],
    images: list[ImageRef],
    links: list[LinkRef],
    panel_ids_by_uid: dict[str, set[str]],
    findings: list[Finding],
) -> None:
    report = {
        "pages": [page.__dict__ for page in pages],
        "iframes": [iframe.__dict__ for iframe in iframes],
        "semantic_iframes": [iframe.__dict__ for iframe in semantic_iframes],
        "images": [image.__dict__ for image in images],
        "links": [link.__dict__ for link in links],
        "grafana": {
            uid: sorted(panel_ids, key=lambda value: int(value) if value.isdigit() else 10**9)
            for uid, panel_ids in panel_ids_by_uid.items()
        },
        "findings": [finding.__dict__ for finding in findings],
    }
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_semantic_report(path: Path, semantic_iframes: list[SemanticIframe]) -> None:
    lines = [
        "# Verdify Site Grafana Semantic Inventory",
        "",
        "Each row maps a Markdown iframe to the nearest preceding heading and the live Grafana panel title.",
        "",
    ]
    current_file = ""
    for iframe in semantic_iframes:
        if iframe.file != current_file:
            current_file = iframe.file
            lines.extend(
                [
                    f"## {current_file}",
                    "",
                    "| Line | Heading | Dashboard | Panel | Live panel title |",
                    "|---:|---|---|---:|---|",
                ]
            )
        lines.append(
            f"| {iframe.line} | {iframe.heading or '-'} | {iframe.dashboard_uid} | "
            f"{iframe.panel_id or '-'} | {iframe.panel_title or 'MISSING'} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--image-manifest", type=Path, default=DEFAULT_IMAGE_MANIFEST)
    parser.add_argument("--grafana-container", default=DEFAULT_GRAFANA_CONTAINER)
    parser.add_argument("--site-container", default=DEFAULT_SITE_CONTAINER)
    parser.add_argument("--skip-grafana", action="store_true")
    parser.add_argument("--skip-public", action="store_true")
    parser.add_argument("--skip-site-container", action="store_true")
    parser.add_argument("--skip-launch-lint", action="store_true")
    parser.add_argument("--json-report", type=Path, help="Write machine-readable report to this path")
    parser.add_argument(
        "--semantic-report",
        type=Path,
        help="Write a Markdown iframe-to-heading-to-live-panel-title inventory",
    )
    parser.add_argument("--warnings-fail", action="store_true", help="Exit nonzero on warnings as well as errors")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    findings: list[Finding] = []

    if not args.vault_root.exists():
        print(f"vault root missing: {args.vault_root}", file=sys.stderr)
        return 2

    image_manifest = load_image_manifest(args.image_manifest)
    pages, iframes, images, links, content_findings = collect_pages(args.vault_root)
    findings.extend(content_findings)
    findings.extend(check_duplicate_routes(pages))
    findings.extend(check_retired_source_paths(args.vault_root))
    findings.extend(check_generation_entrypoint())
    findings.extend(check_generated_route_aliases(args.vault_root))
    findings.extend(check_forecast_freshness(args.vault_root))
    findings.extend(check_hourly_performance_freshness(args.vault_root))
    findings.extend(check_static_snapshot_freshness(args.vault_root))
    findings.extend(check_plan_archive_freshness(args.vault_root))
    findings.extend(check_plan_archive_content(args.vault_root))
    findings.extend(check_duplicate_panels(iframes))
    findings.extend(check_repeated_public_prose(args.vault_root))
    findings.extend(check_canonical_fact_owners(args.vault_root))
    findings.extend(check_images(args.vault_root, images, image_manifest))
    findings.extend(check_links(args.vault_root, links))
    if not args.skip_launch_lint:
        findings.extend(check_launch_lint(args.vault_root, args.public_root, args.skip_public))

    panel_ids_by_uid: dict[str, set[str]] = {}
    panel_titles_by_uid: dict[str, dict[str, str]] = {}
    if not args.skip_grafana:
        panel_ids_by_uid, panel_titles_by_uid, grafana_findings = check_grafana(iframes, args.grafana_container)
        findings.extend(grafana_findings)

    if not args.skip_public:
        findings.extend(check_public_output(args.vault_root, args.public_root))
        findings.extend(check_navigation_coverage(args.vault_root, args.public_root))

    if not args.skip_site_container:
        findings.extend(check_site_container(args.site_container))

    semantic_iframes = collect_semantic_iframes(args.vault_root, iframes, panel_titles_by_uid)

    print_summary(pages, iframes, images, links, panel_ids_by_uid, findings)
    if args.json_report:
        write_json_report(args.json_report, pages, iframes, semantic_iframes, images, links, panel_ids_by_uid, findings)
        print(f"\nJSON report written: {args.json_report}")
    if args.semantic_report:
        write_semantic_report(args.semantic_report, semantic_iframes)
        print(f"\nSemantic report written: {args.semantic_report}")

    has_errors = any(finding.severity == "error" for finding in findings)
    has_warnings = any(finding.severity == "warn" for finding in findings)
    if has_errors or (args.warnings_fail and has_warnings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
