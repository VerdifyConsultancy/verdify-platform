"""Every container base must be immutably pinned.

ADR-0021 moved publishing to the in-cluster Kaniko path and the zot origin, but
the *inputs* to that build were still mutable public tags (`python:3.13-slim`,
`postgres:16-alpine`, ...). A mutable tag means two builds of the same commit
can produce different images, which defeats the immutable-digest contract the
overlays and ArgoCD rely on, and it silently re-points the supply chain
whenever upstream moves a tag.

This guard fails on any `FROM` that is not one of:
  * a digest-pinned reference (`repo:tag@sha256:...`),
  * a reference to an earlier stage in the same Dockerfile, or
  * a build argument (`${...}`), whose value is pinned by the caller.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FROM_RE = re.compile(r"^\s*FROM\s+(?P<ref>\S+)(?:\s+AS\s+(?P<stage>\S+))?", re.IGNORECASE | re.MULTILINE)
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


def _is_dockerfile(path: Path) -> bool:
    """Match `Dockerfile`, `Dockerfile.<variant>` and `<variant>.Dockerfile` only.

    Deliberately narrow: a substring match also catches this test module (and
    its .pyc), whose `from __future__ import ...` then parses as a FROM line.
    """
    name = path.name
    return name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".Dockerfile")


def _dockerfiles() -> list[Path]:
    found = [
        path
        for path in REPO_ROOT.rglob("*")
        if path.is_file()
        and _is_dockerfile(path)
        and ".git" not in path.parts
        and "node_modules" not in path.parts
        and "__pycache__" not in path.parts
    ]
    assert found, "no Dockerfiles discovered — the guard would pass vacuously"
    return sorted(found)


@pytest.mark.parametrize("dockerfile", _dockerfiles(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_every_external_base_image_is_digest_pinned(dockerfile: Path):
    text = dockerfile.read_text(encoding="utf-8")
    stages: set[str] = set()
    unpinned: list[str] = []

    for match in FROM_RE.finditer(text):
        reference = match.group("ref")
        if reference.casefold() in stages:
            pass  # earlier stage in this same file
        elif reference.startswith("$"):
            pass  # build arg; the caller supplies the pinned value
        elif not DIGEST_RE.search(reference):
            unpinned.append(reference)
        if match.group("stage"):
            stages.add(match.group("stage").casefold())

    assert not unpinned, (
        f"{dockerfile.relative_to(REPO_ROOT)} has mutable base reference(s): {unpinned}. "
        "Pin as repo:tag@sha256:<digest> so a rebuild of this commit is reproducible."
    )
