"""dashboard/Dockerfile contract (VD-01, #1121): what path impact can read and what the image runs as.

jvallery/agents ``verdify_path_impact.py`` reads Dockerfiles as data. It joins continuation lines, skips
``COPY --from=``, and turns ``ADD``, any other flag, a dollar sign or a backslash in a ``COPY`` into a rebuild of
every image. ``_path_impact_sources`` applies the same reading to ``dashboard/Dockerfile``.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = REPO_ROOT / "dashboard"
DOCKERFILE = DASHBOARD / "Dockerfile"
NODE_BASE = (
    "docker.io/library/node:24.22.1-bookworm-slim"
    "@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205"
)
DIGEST_PINNED = re.compile(r"@sha256:[0-9a-f]{64}$")
SOURCE_REVISION_COPY = "COPY --from=source-metadata /out/source-revision /etc/verdify/source-revision"


def _text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _path_impact_sources(text: str) -> list[str]:
    """The repository paths path impact reads from plain COPY lines (verdify_path_impact.copies)."""
    result = []
    for raw in text.replace("\\\n", " ").splitlines():
        line = raw.strip()
        if not re.match(r"(?i)^(COPY|ADD)\s", line):
            continue
        command, arguments = line.split(None, 1)
        if command.upper() == "ADD":
            raise ValueError("ADD requires conservative rebuild")
        if arguments.startswith("--from="):
            continue
        if arguments.startswith("--") or "$" in arguments or "\\" in arguments:
            raise ValueError("unresolved COPY syntax")
        parts = json.loads(arguments) if arguments.startswith("[") else shlex.split(arguments)
        if len(parts) < 2:
            raise ValueError("COPY has no source/destination")
        for source in parts[:-1]:
            if source.startswith("/") or ".." in PurePosixPath(source).parts:
                raise ValueError("COPY escapes root context")
            result.append(source.removeprefix("./").rstrip("/"))
    return result


def _stages(text: str) -> list[dict]:
    """Stages in order: base image, stage name and instructions, continuation lines joined, comments dropped."""
    stages: list[dict] = []
    for raw in text.replace("\\\n", " ").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keyword, _, rest = line.partition(" ")
        if keyword.upper() == "FROM":
            words = rest.split()
            name = words[2] if len(words) == 3 and words[1].upper() == "AS" else None
            stages.append({"base": words[0], "name": name, "lines": []})
        else:
            assert stages, f"instruction before FROM: {line}"
            stages[-1]["lines"].append(line)
    return stages


def _keyword(line: str) -> str:
    return line.split(None, 1)[0].upper()


def test_path_impact_reads_every_copy_and_sees_only_dashboard_inputs():
    # Raises on ADD, an unsupported flag, $ or a backslash: path impact would then rebuild every image.
    sources = _path_impact_sources(_text())
    assert sources == [
        "dashboard/package.json",
        "dashboard/pnpm-lock.yaml",
        "dashboard/.npmrc",
        "dashboard",
        ".git",
    ]
    # Only dashboard/** selects this image; .git is the HEAD receipt the root .dockerignore admits.
    assert all(source == ".git" or source == "dashboard" or source.startswith("dashboard/") for source in sources)


def test_copy_lines_are_plain_or_from_an_earlier_stage_and_there_is_no_add():
    seen: list[str] = []
    for stage in _stages(_text()):
        for line in stage["lines"]:
            assert _keyword(line) != "ADD", line
            if _keyword(line) == "COPY":
                flags = [word for word in line.split()[1:] if word.startswith("--")]
                assert flags == [] or (len(flags) == 1 and flags[0].removeprefix("--from=") in seen), line
                assert "$" not in line and "\\" not in line, line
        if stage["name"]:
            seen.append(stage["name"])


def test_every_from_is_digest_pinned_or_names_an_earlier_stage():
    names: list[str] = []
    external = []
    for stage in _stages(_text()):
        if stage["base"] not in names:
            assert DIGEST_PINNED.search(stage["base"]), stage["base"]
            external.append(stage["base"])
        if stage["name"]:
            names.append(stage["name"])
    assert external == [NODE_BASE, NODE_BASE, NODE_BASE]


def test_final_stage_runs_as_a_numeric_non_root_user_from_build_outputs_only():
    final = _stages(_text())[-1]
    users = [line.split()[1] for line in final["lines"] if _keyword(line) == "USER"]
    assert len(users) == 1
    assert re.fullmatch(r"\d+(:\d+)?", users[0]), users
    assert all(int(part) != 0 for part in users[0].split(":"))
    # Nothing runs or mounts in the final stage: it copies build outputs and the receipt, then starts node.
    assert not [line for line in final["lines"] if _keyword(line) in {"RUN", "VOLUME", "ADD", "HEALTHCHECK"}]
    copies = [line for line in final["lines"] if _keyword(line) == "COPY"]
    assert copies and all(line.split()[1].startswith("--from=") for line in copies)
    assert [line for line in final["lines"] if _keyword(line) == "CMD"] == ['CMD ["node", "server.mjs"]']


def test_only_port_3000_is_exposed():
    exposed = [line.split()[1:] for stage in _stages(_text()) for line in stage["lines"] if _keyword(line) == "EXPOSE"]
    assert exposed == [["3000"]]


def test_source_revision_is_baked_into_the_image_and_its_environment():
    stages = {stage["name"]: stage for stage in _stages(_text())}
    metadata = " ".join(stages["source-metadata"]["lines"])
    assert "ARG GIT_SHA=unknown" in metadata
    assert "COPY .git /tmp/verdify-git-metadata" in metadata
    assert "grep -Eq '^[0-9a-f]{40}$'" in metadata
    assert "> /out/source-revision" in metadata
    assert "chmod 0444 /out/source-revision" in metadata
    final = _stages(_text())[-1]["lines"]
    assert SOURCE_REVISION_COPY in final
    assert "ARG GIT_SHA=unknown" in final
    assert "ENV NODE_ENV=production PORT=3000 SOURCE_REVISION=$GIT_SHA" in final
    # The server reads the same path when SOURCE_REVISION is not 40 hex.
    assert 'SOURCE_REVISION_PATH = "/etc/verdify/source-revision"' in (DASHBOARD / "server.mjs").read_text()


def test_final_stage_carries_every_local_module_the_server_imports():
    """A module server.mjs imports (the BFF's bff/, #1141) needs its own COPY --from=build line."""
    final = _stages(_text())[-1]["lines"]
    copied: set[str] = set()
    for line in final:
        if line.startswith("COPY --from=build "):
            copied.update(PurePosixPath(source).name for source in line.split()[2:-1])
    server = (DASHBOARD / "server.mjs").read_text(encoding="utf-8")
    imported = {
        PurePosixPath(path).parts[0] for path in re.findall(r"""(?:from\s+|import\()\s*["'](\./[^"']+)["']""", server)
    }
    assert imported == {"security-headers.mjs", "build"}
    assert imported <= copied
    assert {"node_modules", "package.json", "server.mjs"} <= copied
    for name in ("package.json", "server.mjs", "security-headers.mjs"):
        assert (DASHBOARD / name).is_file(), name
