#!/usr/bin/env python3
"""Pragmatic lint for Verdify public-site content.

This is intentionally cheaper and more opinionated than site-doctor: it checks
public-facing content smells in the vault and, when available, built public routes.
It does not query Grafana.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DEFAULT_VAULT = Path("/mnt/iris/verdify-vault/website")
DEFAULT_PUBLIC = Path("/srv/verdify/verdify-site/public")
IMAGE_EXT_RE = re.compile(r"\.(?:avif|gif|jpe?g|png|svg|webp)(?:[?#][^)\s\"']*)?$", re.IGNORECASE)
RAW_WIKI_RE = re.compile(r"\[\[([^\]]+)\]\]")
EMPTY_HEADING_RE = re.compile(r"^#{1,6}\s*$", re.MULTILINE)
HOME_PANEL_CARD_RE = re.compile(
    r"<figure\b[^>]*class=[\"'][^\"']*\bhome-panel-card\b[^\"']*[\"'][^>]*>(.*?)</figure>",
    re.IGNORECASE | re.DOTALL,
)
HOME_IFRAME_RE = re.compile(r"<iframe\b", re.IGNORECASE)
STALE_SNAPSHOT_RE = re.compile(
    r"(?:Static public proof snapshot|Static public API snapshot|Last public proof snapshot):\s*\*{0,2}(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
BLANK_DAILY_LABEL_RE = re.compile(r"\b(?:high|low|vpd_h|hyst|d_cool|engage|all|pulse|gap|wt)\s+·(?=;|,|<|$)")
PLAN_INTERNAL_NAME_RE = re.compile(r"\b(?:Hermes|OpenClaw|local Gemma)\b", re.IGNORECASE)
STALE_CROP_STATUS_RE = re.compile(
    r"\bLaunch Taxonomy\b|For launch,\s+Verdify separates crop pages|No explicit launch taxonomy",
    re.IGNORECASE,
)
STALE_LAUNCH_FRAMING_RE = re.compile(
    r"\blaunch-(?:safe|facing|readable)\b|"
    r"\blaunch\s+(?:traffic|readers|proof|claim|question|thread)\b|"
    r"\bFor launch\b",
    re.IGNORECASE,
)
STALE_TUNABLES_LABEL_RE = re.compile(
    r"AI-[Ww]ritable Tunables|\[AI Tunables\]\(/reference/ai-tunables/?\)",
    re.IGNORECASE,
)
STALE_DATA_MODEL_RE = re.compile(
    r"maps 172 entities|Twelve periodic tasks|tables:\s*47|views:\s*56|functions:\s*100|"
    r"221K\+|39K\+|516K\+|weather_forecast`\s*\|\s*Hypertable\s*\|\s*28K\+|"
    r"2026-05-02 18:09 MDT|all 33 equipment items",
    re.IGNORECASE,
)
LEGACY_CANONICAL_LINK_RE = re.compile(
    r"\]\((/(?:"
    r"intelligence(?:/[^)#\s]*)?|"
    r"evidence(?:/[^)#\s]*)?|"
    r"forecast/?|"
    r"slack/?|"
    r"ai-greenhouse/?|"
    r"climate/?|"
    r"about/?|"
    r"press/?|"
    r"plans/?"
    r")(?:#[^)]+)?)\)",
    re.IGNORECASE,
)
SOURCE_MAP_COMMENT_RE = re.compile(r"sourceMappingURL=", re.IGNORECASE)
LOCAL_FILESYSTEM_PATH_RE = re.compile(r"/mnt/[A-Za-z0-9_./-]+")
HIDDEN_FOLDER_LISTING_RE = re.compile(r"\b0 items under this folder\b", re.IGNORECASE)
STALE_LETTUCE_SEEDLING_RE = re.compile(r"lettuce\s+.\s+seedling", re.IGNORECASE)
LONG_PLANTING_AGE_RE = re.compile(r"\b(?:[89]\d|\d{3,}) days in place\b", re.IGNORECASE)
ROUTE_SMOKE = [
    "/",
    "/ai-greenhouse",
    "/greenhouse",
    "/greenhouse/cameras",
    "/greenhouse/growing",
    "/greenhouse/operations",
    "/crops",
    "/growing",
    "/climate",
    "/intelligence",
    "/intelligence/faq",
    "/faq",
    "/evidence",
    "/plans",
    "/forecast",
    "/contact",
    "/start/ai-greenhouse",
    "/start/climate",
    "/start/evidence",
    "/start/resource-use",
    "/start/slack-ops",
    "/start/about",
    "/start/contact",
    "/data/operations",
    "/data/planning-quality",
    "/data/baseline-vs-iris",
    "/data/economics",
    "/data/plans",
    "/data/forecast",
    "/data/slack-ops",
    "/reference/intelligence",
    "/reference/planning-loop",
    "/reference/context-window",
    "/reference/architecture",
    "/reference/safety",
    "/reference/data-model",
    "/reference/build-notes",
    "/reference/known-limits",
    "/reference/firmware-change-protocol",
    "/reference/related-work",
    "/reference/faq",
    "/reference/lessons",
]


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    message: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def markdown_pages(vault_root: Path) -> list[Path]:
    return sorted(vault_root.rglob("*.md"))


def normalize_route(route: str) -> str:
    clean = route.split("?", 1)[0].split("#", 1)[0].strip()
    clean = clean.removeprefix("/")
    clean = clean.removesuffix("/")
    clean = clean.removesuffix(".html")
    clean = clean.removesuffix(".md")
    return clean or "index"


def frontmatter_aliases(text: str) -> list[str]:
    if not text.startswith("---\n"):
        return []
    end = text.find("\n---", 4)
    if end == -1:
        return []
    raw = text[4:end]
    aliases: list[str] = []
    in_aliases = False
    for line in raw.splitlines():
        if line.startswith((" ", "\t")) and in_aliases:
            value = line.strip()
            if value.startswith("- "):
                aliases.append(value[2:].strip().strip("'\""))
            continue
        in_aliases = False
        if not line.startswith("aliases:"):
            continue
        value = line.split(":", 1)[1].strip()
        if value.startswith("[") and value.endswith("]"):
            aliases.extend(item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip())
        else:
            in_aliases = True
    return aliases


def route_to_source(vault_root: Path, route: str) -> Path | None:
    clean = route.strip("/")
    if not clean:
        candidates = [vault_root / "index.md"]
    else:
        candidates = [
            vault_root / clean / "index.md",
            (vault_root / clean).with_suffix(".md"),
        ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    normalized_route = normalize_route(route)
    for page in markdown_pages(vault_root):
        if any(normalize_route(alias) == normalized_route for alias in frontmatter_aliases(read_text(page))):
            return page
    return None


def route_to_public_candidates(public_root: Path, route: str) -> list[Path]:
    clean = route.strip("/")
    if not clean:
        return [public_root / "index.html"]
    return [public_root / clean / "index.html", (public_root / clean).with_suffix(".html")]


def resolve_asset(vault_root: Path, page: Path, ref: str) -> Path:
    clean = ref.split("?", 1)[0].split("#", 1)[0]
    if clean.startswith("/"):
        return vault_root / clean.lstrip("/")
    return page.parent / clean


def resolve_wiki_ref(vault_root: Path, page: Path, ref: str) -> Path | None:
    clean = ref.split("|", 1)[0].split("#", 1)[0].strip()
    if not clean:
        return page
    if clean.endswith("/"):
        clean = clean.rstrip("/")

    if clean.startswith("/"):
        rel_candidates = [Path(clean.lstrip("/"))]
    else:
        rel_candidates = [page.parent.relative_to(vault_root) / clean, Path(clean)]

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


def image_refs(text: str) -> list[tuple[int, str]]:
    refs: list[tuple[int, str]] = []
    patterns = [
        r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)",
        r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>",
        r"<meta\b[^>]*\b(?:property|name)=[\"'](?:og:image|twitter:image)[\"'][^>]*\bcontent=[\"']([^\"']+)[\"']",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            refs.append((line_number(text, match.start()), match.group(1).strip()))
    return refs


def csv_refs(text: str) -> set[str]:
    refs: set[str] = set()
    for match in re.finditer(r"(?<!!)\[[^\]]+\]\(([^)]+\.csv(?:[?#][^)]+)?)\)", text, flags=re.IGNORECASE):
        refs.add(match.group(1).strip())
    for match in re.finditer(r"https?://api\.verdify\.ai/[^\s\"')]+\.csv", text, flags=re.IGNORECASE):
        refs.add(match.group(0))
    for match in re.finditer(r"(?<![A-Za-z0-9_./-])(/[^\s\"')]+\.csv(?:[?#][^\s\"')]+)?)", text, flags=re.IGNORECASE):
        refs.add(match.group(1).strip())
    return refs


def check_content(vault_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    today = date.today()
    for page in markdown_pages(vault_root):
        rel = page.relative_to(vault_root).as_posix()
        text = read_text(page)

        for match in RAW_WIKI_RE.finditer(text):
            ref = match.group(1)
            if resolve_wiki_ref(vault_root, page, ref) is not None:
                continue
            findings.append(
                Finding(
                    "error",
                    "unresolved-wiki-link",
                    f"{rel}:{line_number(text, match.start())} contains unresolved wiki link [[{ref}]]",
                )
            )

        for match in EMPTY_HEADING_RE.finditer(text):
            findings.append(
                Finding("error", "empty-heading", f"{rel}:{line_number(text, match.start())} contains an empty heading")
            )

        for match in STALE_SNAPSHOT_RE.finditer(text):
            try:
                snapshot_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            if snapshot_date < today:
                findings.append(
                    Finding(
                        "warn",
                        "stale-snapshot-label",
                        f"{rel}:{line_number(text, match.start())} snapshot label is {snapshot_date}, before {today}",
                    )
                )

        for match in LEGACY_CANONICAL_LINK_RE.finditer(text):
            findings.append(
                Finding(
                    "error",
                    "legacy-canonical-link",
                    f"{rel}:{line_number(text, match.start())} links to legacy route {match.group(1)}",
                )
            )

        for match in STALE_CROP_STATUS_RE.finditer(text):
            findings.append(
                Finding(
                    "error",
                    "stale-crop-status-language",
                    f"{rel}:{line_number(text, match.start())} uses obsolete crop-control launch language",
                )
            )

        for match in STALE_LAUNCH_FRAMING_RE.finditer(text):
            findings.append(
                Finding(
                    "error",
                    "stale-launch-framing",
                    f"{rel}:{line_number(text, match.start())} uses temporal launch framing on an evergreen page",
                )
            )

        for match in STALE_TUNABLES_LABEL_RE.finditer(text):
            findings.append(
                Finding(
                    "error",
                    "stale-tunables-label",
                    f"{rel}:{line_number(text, match.start())} should link as AI Tunables Traceability",
                )
            )

        for match in STALE_DATA_MODEL_RE.finditer(text):
            findings.append(
                Finding(
                    "error",
                    "stale-data-model-count",
                    f"{rel}:{line_number(text, match.start())} uses a stale data-model count or sample export marker",
                )
            )

        if rel.startswith("plans/") and rel != "plans/index.md":
            for match in PLAN_INTERNAL_NAME_RE.finditer(text):
                findings.append(
                    Finding(
                        "error",
                        "daily-plan-internal-name",
                        f"{rel}:{line_number(text, match.start())} exposes internal planner implementation name '{match.group(0)}'",
                    )
                )
            for match in BLANK_DAILY_LABEL_RE.finditer(text):
                findings.append(
                    Finding(
                        "error",
                        "daily-plan-blank-label",
                        f"{rel}:{line_number(text, match.start())} contains blank daily-plan label '{match.group(0).strip()}'",
                    )
                )

        for line_no, ref in image_refs(text):
            if ref.startswith(("http://", "https://", "data:")):
                continue
            if not IMAGE_EXT_RE.search(ref.split("?", 1)[0].split("#", 1)[0]):
                findings.append(Finding("error", "malformed-image-ref", f"{rel}:{line_no} malformed image ref {ref}"))
                continue
            if not resolve_asset(vault_root, page, ref).exists():
                findings.append(Finding("error", "missing-image", f"{rel}:{line_no} image does not exist: {ref}"))

        for line_no, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if (
                ".jpg" in lowered
                and "![" not in line
                and "<img" not in lowered
                and "poster=" not in lowered
                and "<a " not in lowered
                and "og:image" not in lowered
                and "socialimage:" not in lowered
            ):
                findings.append(
                    Finding("warn", "bare-jpg-reference", f"{rel}:{line_no} contains .jpg outside an image tag/link")
                )

    return findings


def check_homepage_panel_cards(vault_root: Path) -> list[Finding]:
    path = vault_root / "index.md"
    if not path.exists():
        return [Finding("error", "homepage-missing", "index.md is missing")]

    text = read_text(path)
    findings: list[Finding] = []
    cards = list(HOME_PANEL_CARD_RE.finditer(text))
    iframes = list(HOME_IFRAME_RE.finditer(text))

    if len(cards) != len(iframes):
        findings.append(
            Finding(
                "error",
                "homepage-panel-card-count",
                f"index.md has {len(iframes)} iframe(s) but {len(cards)} home-panel-card container(s)",
            )
        )

    for card in cards:
        block = card.group(1)
        line_no = line_number(text, card.start())
        if 'class="home-panel-card__visual"' not in block:
            findings.append(
                Finding("error", "homepage-panel-visual-missing", f"index.md:{line_no} panel card has no visual well")
            )
        if not HOME_IFRAME_RE.search(block):
            findings.append(
                Finding("error", "homepage-panel-iframe-missing", f"index.md:{line_no} panel card has no iframe")
            )
        if 'class="home-panel-card__intro"' not in block:
            findings.append(
                Finding(
                    "error", "homepage-panel-intro-missing", f"index.md:{line_no} panel card has no intro narrative"
                )
            )
        if not re.search(r"<h3(?:\s[^>]*)?>", block):
            findings.append(
                Finding("error", "homepage-panel-title-missing", f"index.md:{line_no} panel card has no section title")
            )

    return findings


def check_routes(vault_root: Path, public_root: Path, skip_public: bool) -> list[Finding]:
    findings: list[Finding] = []
    for route in ROUTE_SMOKE:
        source = route_to_source(vault_root, route)
        if source is None:
            findings.append(
                Finding("error", "route-source-missing", f"required public route has no source page: {route}")
            )
            continue
        if not skip_public and public_root.exists():
            if not any(candidate.exists() for candidate in route_to_public_candidates(public_root, route)):
                findings.append(
                    Finding("warn", "route-public-missing", f"required public route has no built HTML: {route}")
                )
    return findings


def check_public_rendered(public_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    if not public_root.exists():
        return findings

    for page in sorted(public_root.rglob("*.html")):
        rel = page.relative_to(public_root).as_posix()
        text = read_text(page)

        if SOURCE_MAP_COMMENT_RE.search(text):
            findings.append(Finding("error", "public-source-map-comment", f"{rel} contains a sourceMappingURL comment"))

        match = LOCAL_FILESYSTEM_PATH_RE.search(text)
        if match:
            findings.append(
                Finding("error", "public-local-path", f"{rel} exposes local-looking filesystem path {match.group(0)}")
            )

        if HIDDEN_FOLDER_LISTING_RE.search(text):
            findings.append(
                Finding(
                    "error",
                    "public-hidden-folder-listing",
                    f"{rel} renders hidden Quartz folder-listing text",
                )
            )

        if STALE_LETTUCE_SEEDLING_RE.search(text) and LONG_PLANTING_AGE_RE.search(text):
            findings.append(
                Finding(
                    "error",
                    "public-stale-crop-stage",
                    f"{rel} renders lettuce as seedling after an implausibly long planting age",
                )
            )

    return findings


def check_csvs(vault_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    refs: set[str] = set()
    for page in markdown_pages(vault_root):
        refs.update(ref for ref in csv_refs(read_text(page)) if not ref.startswith("http"))
    refs.update(path.relative_to(vault_root).as_posix() for path in (vault_root / "static" / "data").glob("*.csv"))

    for ref in sorted(refs):
        path = vault_root / ref.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        if not path.exists():
            findings.append(Finding("error", "csv-missing", f"CSV reference is missing: {ref}"))
            continue
        if path.stat().st_size == 0:
            findings.append(Finding("error", "csv-empty", f"CSV is empty: {ref}"))
            continue
        with path.open(newline="", encoding="utf-8", errors="replace") as fh:
            rows = list(csv.reader(fh))
        if len(rows) < 2:
            findings.append(Finding("error", "csv-no-data", f"CSV has no data rows: {ref}"))
            continue
        if len(rows[0]) < 2:
            findings.append(Finding("error", "csv-narrow-header", f"CSV header has fewer than 2 columns: {ref}"))
    return findings


def print_summary(findings: list[Finding]) -> None:
    errors = sum(1 for finding in findings if finding.severity == "error")
    warnings = sum(1 for finding in findings if finding.severity == "warn")
    print(f"Verdify public-site lint: {len(findings)} findings ({errors} errors, {warnings} warnings)")
    for finding in sorted(findings, key=lambda f: (f.severity != "error", f.code, f.message)):
        print(f"  [{finding.severity}] {finding.code}: {finding.message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--skip-public", action="store_true")
    parser.add_argument("--json-report", type=Path)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--warnings-fail", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.vault_root.exists():
        print(f"vault root missing: {args.vault_root}", file=sys.stderr)
        return 2

    findings: list[Finding] = []
    findings.extend(check_content(args.vault_root))
    findings.extend(check_homepage_panel_cards(args.vault_root))
    findings.extend(check_routes(args.vault_root, args.public_root, args.skip_public))
    if not args.skip_public:
        findings.extend(check_public_rendered(args.public_root))
    findings.extend(check_csvs(args.vault_root))

    if args.json_report:
        args.json_report.write_text(
            json.dumps({"findings": [finding.__dict__ for finding in findings]}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if not args.quiet:
        print_summary(findings)

    has_errors = any(finding.severity == "error" for finding in findings)
    has_warnings = any(finding.severity == "warn" for finding in findings)
    if has_errors or (args.warnings_fail and has_warnings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
