"""Immutable base-image contract for the experiment-v2 release build closure."""

from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
PYTHON_BASE = "python:3.13-slim@sha256:ffb752e139c0a19692a43af8d8523b274222dd68eebad5d583b45c2201c6e30a"
POSTGRES_BASE = "postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685"

EXPECTED_EXTERNAL_FROM = {
    "api/Dockerfile": [PYTHON_BASE],
    "ingestor/Dockerfile": [PYTHON_BASE],
    "mcp/Dockerfile": [PYTHON_BASE],
    "db/Dockerfile.migrate": [POSTGRES_BASE],
    "experiment_orchestrator/Dockerfile": [PYTHON_BASE, PYTHON_BASE],
}


def _external_from_images(path: Path) -> list[str]:
    aliases: set[str] = set()
    external: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        words = raw_line.split()
        if not words or words[0].upper() != "FROM":
            continue
        image = words[1]
        if image not in aliases:
            external.append(image)
        if len(words) >= 4 and words[-2].upper() == "AS":
            aliases.add(words[-1])
    return external


def test_release_build_closure_uses_exact_immutable_base_images():
    observed = {relative: _external_from_images(REPO_ROOT / relative) for relative in EXPECTED_EXTERNAL_FROM}
    assert observed == EXPECTED_EXTERNAL_FROM
