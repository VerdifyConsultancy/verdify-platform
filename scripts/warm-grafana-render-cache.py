#!/srv/greenhouse/.venv/bin/python
"""Warm Grafana PNG render cache for mobile website embeds.

The Quartz site emits `.grafana-embed` placeholders with a
`data-image-src` URL and `data-height`. Mobile clients use stable
bucketed render dimensions so nginx/Grafana can cache the expensive
headless-Chromium PNG render. This script pre-renders those mobile
URLs from the built public site.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

DEFAULT_PUBLIC_ROOT = Path("/srv/verdify/verdify-site/public")
DEFAULT_TIMEOUT_S = 75
DEFAULT_WORKERS = 1
DEFAULT_BUCKETS = (800,)
DEFAULT_RETRIES = 1
DEFAULT_RETRY_DELAY_S = 5.0
DEFAULT_FAILURE_THRESHOLD_PCT = 0.0
TRANSIENT_HTTP_STATUS = {429, 500, 502, 503, 504}
ASSUMED_CSS_WIDTH_BY_BUCKET = {
    800: 390,
    1000: 540,
    1200: 740,
    1440: 1000,
}
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 "
    "Mobile/15E148 Safari/604.1"
)

EMBED_RE = re.compile(r'<div\s+class="grafana-embed"(?P<attrs>[^>]*)>', re.IGNORECASE)
ATTR_RE = re.compile(r'(?P<name>[\w:-]+)(?:=(?P<quote>["\'])(?P<value>.*?)(?P=quote))?')


@dataclass(frozen=True)
class WarmResult:
    url: str
    status: int | None
    cache_status: str
    elapsed_s: float
    attempts: int = 1
    error: str | None = None


def parse_attrs(attr_text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in ATTR_RE.finditer(attr_text):
        name = match.group("name")
        value = match.group("value")
        if value is not None:
            attrs[name] = html.unescape(value)
    return attrs


def mobile_render_url(image_src: str, panel_height: int, bucket: int) -> str:
    assumed_width = ASSUMED_CSS_WIDTH_BY_BUCKET[bucket]
    render_height = max(160, round(panel_height * (bucket / assumed_width)))

    parts = urlsplit(image_src)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["width"] = str(bucket)
    query["height"] = str(render_height)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def collect_urls(public_root: Path, buckets: tuple[int, ...]) -> list[str]:
    urls: set[str] = set()
    for html_file in sorted(public_root.rglob("*.html")):
        text = html_file.read_text(encoding="utf-8", errors="replace")
        for match in EMBED_RE.finditer(text):
            attrs = parse_attrs(match.group("attrs"))
            image_src = attrs.get("data-image-src")
            if not image_src:
                continue
            try:
                panel_height = int(attrs.get("data-height", "300"))
            except ValueError:
                panel_height = 300
            for bucket in buckets:
                urls.add(mobile_render_url(image_src, panel_height, bucket))
    return sorted(urls)


def warm_once(url: str, timeout_s: int, attempt: int) -> WarmResult:
    start = time.monotonic()
    req = Request(url, method="HEAD", headers={"User-Agent": MOBILE_UA})
    try:
        with urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - public Grafana URL.
            elapsed_s = time.monotonic() - start
            return WarmResult(
                url=url,
                status=resp.status,
                cache_status=resp.headers.get("X-Cache-Status", ""),
                elapsed_s=elapsed_s,
                attempts=attempt,
            )
    except HTTPError as exc:
        elapsed_s = time.monotonic() - start
        return WarmResult(
            url=url, status=exc.code, cache_status="", elapsed_s=elapsed_s, attempts=attempt, error=str(exc)
        )
    except URLError as exc:
        elapsed_s = time.monotonic() - start
        return WarmResult(
            url=url, status=None, cache_status="", elapsed_s=elapsed_s, attempts=attempt, error=str(exc.reason)
        )
    except TimeoutError as exc:
        elapsed_s = time.monotonic() - start
        return WarmResult(url=url, status=None, cache_status="", elapsed_s=elapsed_s, attempts=attempt, error=str(exc))


def warm_url(url: str, timeout_s: int, retries: int, retry_delay_s: float) -> WarmResult:
    attempts = max(1, retries + 1)
    result = warm_once(url, timeout_s, attempt=1)
    for attempt in range(2, attempts + 1):
        retryable = result.status in TRANSIENT_HTTP_STATUS or result.status is None
        if result.error is None or not retryable:
            return result
        time.sleep(max(0.0, retry_delay_s) * (attempt - 1))
        result = warm_once(url, timeout_s, attempt=attempt)
    return result


def parse_buckets(raw: str) -> tuple[int, ...]:
    buckets: list[int] = []
    for part in raw.split(","):
        bucket = int(part.strip())
        if bucket not in ASSUMED_CSS_WIDTH_BY_BUCKET:
            raise argparse.ArgumentTypeError(
                f"unsupported bucket {bucket}; choose from {sorted(ASSUMED_CSS_WIDTH_BY_BUCKET)}"
            )
        buckets.append(bucket)
    return tuple(dict.fromkeys(buckets))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC_ROOT)
    parser.add_argument("--buckets", type=parse_buckets, default=DEFAULT_BUCKETS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--retry-delay", type=float, default=DEFAULT_RETRY_DELAY_S)
    parser.add_argument(
        "--failure-threshold-pct",
        type=float,
        default=DEFAULT_FAILURE_THRESHOLD_PCT,
        help="allow this percentage of transient render failures before exiting non-zero",
    )
    parser.add_argument("--limit", type=int, default=0, help="limit URLs for smoke tests")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    urls = collect_urls(args.public_root, args.buckets)
    if args.limit:
        urls = urls[: args.limit]

    print(f"Grafana render cache warmer: {len(urls)} URL(s), buckets={','.join(map(str, args.buckets))}")
    if args.dry_run:
        for url in urls:
            print(url)
        return 0

    failures = 0
    cache_counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(warm_url, url, args.timeout, args.retries, args.retry_delay) for url in urls]
        for idx, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            cache_key = result.cache_status or "NONE"
            cache_counts[cache_key] = cache_counts.get(cache_key, 0) + 1
            ok = result.status == 200 and result.error is None
            if not ok:
                failures += 1
            print(
                f"[{idx}/{len(urls)}] status={result.status or '-'} "
                f"cache={cache_key} attempts={result.attempts} elapsed={result.elapsed_s:.1f}s {result.url}"
            )
            if result.error:
                print(f"  error: {result.error}", file=sys.stderr)

    summary = ", ".join(f"{key}={value}" for key, value in sorted(cache_counts.items()))
    print(f"Grafana render cache warmer complete: failures={failures}; {summary}")
    failure_pct = (failures / len(urls) * 100.0) if urls else 0.0
    if failures and failure_pct <= max(0.0, args.failure_threshold_pct):
        print(f"Grafana render cache warmer tolerated {failures}/{len(urls)} failures ({failure_pct:.1f}%)")
        return 0
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
