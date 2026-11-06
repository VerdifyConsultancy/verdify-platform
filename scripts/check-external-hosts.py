#!/usr/bin/env python3
"""No new hard-coded public hostnames in Verdify runtime source (#1351; Jason 2026-11-05:
Verdify runs local-only).

Scans the tracked files under SCAN_PATHS for URLs (http, https, ws, wss) and fails on any public
host that deploy/external-hosts.yaml does not list with its decision. Estate and in-cluster names
(vallery.net, verdify.ai, *.svc, *.local, localhost, IP literals) are local. Comment-only lines are
skipped, so documentation links in comments do not need a row. The allow-list also fails when it
lists a host that no scanned file names any more, so retired rows get removed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / "deploy/external-hosts.yaml"
SCAN_PATHS = (
    "Makefile",
    "api",
    "config",
    "dashboard/app",
    "dashboard/bff",
    "dashboard/Dockerfile",
    "dashboard/security-headers.mjs",
    "dashboard/server.mjs",
    "db",
    "deploy",
    "experiment_orchestrator",
    "firmware",
    "hermes",
    "ingestor",
    "mcp",
    "planner_graph",
    "scripts",
    "site/quartz",
    "site/quartz.config.ts",
    "twin",
    "verdify_public",
    "verdify_schemas",
    "verdify_services",
    ":(top,glob)*.py",
)
SUFFIXES = {
    "",
    ".py",
    ".sh",
    ".yaml",
    ".yml",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".cjs",
    ".json",
    ".conf",
    ".j2",
    ".sql",
    ".toml",
    ".h",
    ".c",
    ".cpp",
    ".ini",
    ".cfg",
    ".css",
    ".html",
    ".tpl",
}
SKIP_NAMES = {"uv.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "external-hosts.yaml"}
URL = re.compile(r"\b(?:https?|wss?)://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
LOCAL_SUFFIXES = (
    ".vallery.net",
    ".verdify.ai",
    ".svc",
    ".local",
    ".internal.",
    ".localhost",
    ".example.com",
    ".example.org",
    ".invalid",
    ".test",
    ".example",
)
LOCAL_NAMES = {"vallery.net", "verdify.ai", "localhost", "example.com", "example.org"}
COMMENT = ("#", "//", "/*", "*", "<!--", "--", ";")


def is_local(host: str) -> bool:
    if host in LOCAL_NAMES or host.endswith(LOCAL_SUFFIXES):
        return True
    return bool(re.fullmatch(r"[0-9.]+", host))


def tracked_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files", "-z", "--", *SCAN_PATHS], cwd=ROOT)
    return [name for name in out.decode().split("\0") if name]


def scan(files: list[str], root: Path = ROOT) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name in files:
        path = root / name
        if path.name in SKIP_NAMES or path.suffix not in SUFFIXES or "node_modules" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith(COMMENT):
                continue
            for host in URL.findall(line):
                host = host.lower().rstrip(".")
                if not is_local(host):
                    found.setdefault(host, []).append(f"{name}:{number}")
    return found


def check(found: dict[str, list[str]], allowed: dict) -> list[str]:
    errors = [
        f"{host} ({', '.join(where[:3])}) is not in deploy/external-hosts.yaml; Verdify runs local-only (#1351)"
        for host, where in sorted(found.items())
        if host not in allowed
    ]
    errors += [
        f"deploy/external-hosts.yaml lists {host}, which no scanned file names: remove the row"
        for host in sorted(set(allowed) - set(found))
    ]
    for host, row in allowed.items():
        if not isinstance(row, dict) or not row.get("scope") or not row.get("decision"):
            errors.append(f"deploy/external-hosts.yaml {host}: needs scope and decision")
    return errors


def main() -> int:
    allowed = (yaml.safe_load(ALLOWLIST.read_text()) or {}).get("hosts") or {}
    errors = check(scan(tracked_files()), allowed)
    if errors:
        sys.stderr.write("External host guard:\n  " + "\n  ".join(errors) + "\n")
        return 1
    print(f"external hosts OK ({len(allowed)} allow-listed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
