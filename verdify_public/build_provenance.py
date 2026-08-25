"""Resolve the immutable source revision baked into a Verdify image."""

from __future__ import annotations

import os
import re
from pathlib import Path

SOURCE_REVISION_PATH = Path("/etc/verdify/source-revision")
_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def image_git_sha(
    env_value: str | None = None,
    source_revision_path: Path = SOURCE_REVISION_PATH,
) -> str:
    """Return a verified 40-hex build argument or baked managed-CI receipt."""

    candidate = (env_value if env_value is not None else os.environ.get("VERDIFY_GIT_SHA", "")).strip()
    if _FULL_GIT_SHA.fullmatch(candidate):
        return candidate
    try:
        candidate = source_revision_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return "unknown"
    return candidate if _FULL_GIT_SHA.fullmatch(candidate) else "unknown"
