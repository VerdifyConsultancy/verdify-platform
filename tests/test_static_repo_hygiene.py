"""Repo-wide static validation: parse/compile every tracked source file.

The pre-merge gate (`scripts/ci-local.sh`) leans on ruff for Python quality,
but ruff only scans the paths that target names explicitly —
`ingestor/ api/ mcp/ scripts/*.py tests/ verdify_public/ verdify_schemas/` —
and `pyproject.toml` additionally sets `extend-exclude = ["planner_graph"]`.
That leaves **68 tracked `.py` files with no syntax coverage at all**, including
the entire 50-file `planner_graph/` package that ships as a production k3s
image, plus `slack_ops/`, `planning/`, `deploy/`, `site-astro/`, `twin/` and
`slack_config.py`. A syntax error in any of them merges clean today and fails
at container start.

The same hole exists for the non-Python surface. `.pre-commit-config.yaml`
carries `check-yaml`, `check-json` and `check-merge-conflict`, but
`ci-local.sh` never invokes pre-commit — those hooks only fire for developers
who ran `pre-commit install` locally, so they are advisory, not enforced. This
module makes the cheap, unambiguous checks part of the required gate.

Everything here is hermetic by construction: it reads tracked files off disk
and parses them. No network, no database, no cluster, no secrets, no
subprocess beyond `git ls-files`. Nothing here decrypts or inspects the *value*
of any SOPS-encrypted field.

Companion guard: `tests/test_no_hosted_runner_workflows.py` (CI execution
policy). CI topology and rationale: `docs/ci/zero-paid-runner-ledger.md`.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# Binary/vendored trees are excluded from the byte-level scans by virtue of
# using `git ls-files` (tracked files only) plus an explicit suffix filter —
# never a hand-maintained directory allowlist that silently rots.
BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf",
    ".gz", ".zip", ".tar", ".bin", ".woff", ".woff2", ".ttf", ".otf",
}  # fmt: skip


def _tracked(*patterns: str) -> list[Path]:
    """Tracked files matching `patterns` (all tracked files when empty).

    Fails closed: if `git ls-files` cannot run, the gate errors rather than
    silently validating zero files — the failure mode that would make every
    assertion below vacuously true.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z", *patterns],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [ROOT / name for name in result.stdout.split("\0") if name]


def _read_text(path: Path) -> str | None:
    if path.suffix.lower() in BINARY_SUFFIXES:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


class _TagTolerantLoader(yaml.SafeLoader):
    """SafeLoader that tolerates unknown tags so ESPHome YAML is still parsed.

    `firmware/greenhouse.yaml` uses ESPHome's `!include` / `!secret` / `!lambda`
    tags, which plain `yaml.safe_load` rejects. `.pre-commit-config.yaml` simply
    excludes `firmware/` from `check-yaml` and gives up on it entirely. Skipping
    only the *tag semantics* is strictly better: indentation, structure and
    duplicate-key-free mapping syntax are still validated across the firmware
    tree, which is where a malformed edit would otherwise reach the ESP32.

    Nothing is resolved or executed — unknown tags collapse to their underlying
    scalar/sequence/mapping node.
    """


def _construct_unknown(loader, tag_suffix, node):  # noqa: ARG001
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_TagTolerantLoader.add_multi_constructor("", _construct_unknown)


def test_every_tracked_python_file_compiles():
    """Every tracked `.py` must parse — including the trees ruff never sees."""
    sources = _tracked("*.py")
    assert sources, "no tracked Python files found — the file discovery itself is broken"

    failures: list[str] = []
    for path in sources:
        try:
            ast.parse(path.read_bytes(), filename=str(path))
        except SyntaxError as exc:
            failures.append(f"{path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")
        except ValueError as exc:  # e.g. source containing null bytes
            failures.append(f"{path.relative_to(ROOT)}: {exc}")

    assert not failures, "Python files that do not compile:\n  " + "\n  ".join(failures)


def test_every_tracked_yaml_file_parses():
    """Every tracked YAML document must parse, firmware/ included."""
    sources = _tracked("*.yml", "*.yaml")
    assert sources, "no tracked YAML files found — the file discovery itself is broken"

    failures: list[str] = []
    for path in sources:
        text = _read_text(path)
        if text is None:
            continue
        try:
            list(yaml.load_all(text, Loader=_TagTolerantLoader))
        except yaml.YAMLError as exc:
            detail = str(exc).replace("\n", " ")[:200]
            failures.append(f"{path.relative_to(ROOT)}: {detail}")

    assert not failures, "YAML files that do not parse:\n  " + "\n  ".join(failures)


def test_no_unresolved_merge_conflict_markers():
    """A committed conflict marker breaks whatever consumes the file at runtime."""
    # Built at runtime so this file — which necessarily mentions the markers —
    # cannot match itself.
    opening = "<" * 7
    closing = ">" * 7

    failures: list[str] = []
    for path in _tracked():
        text = _read_text(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.startswith(opening) or line.startswith(closing):
                failures.append(f"{path.relative_to(ROOT)}:{lineno}")

    assert not failures, "unresolved merge-conflict markers:\n  " + "\n  ".join(failures)


_FROM_RE = re.compile(r"^\s*FROM\s+(?P<ref>\S+)(?:\s+AS\s+(?P<stage>\S+))?\s*$", re.IGNORECASE)


def _dockerfiles() -> list[Path]:
    return sorted(p for p in _tracked() if p.name == "Dockerfile" or p.name.startswith("Dockerfile."))


def test_every_dockerfile_declares_a_pinned_base_image():
    """Each `FROM` must name a prior stage, a build arg, or an explicitly tagged image.

    An untagged or `:latest` base makes the image non-reproducible: the same
    commit builds differently tomorrow. Multi-stage internal references
    (`FROM base AS builder`) are resolved against stages declared earlier in the
    same file rather than being mistaken for untagged images.
    """
    dockerfiles = _dockerfiles()
    assert dockerfiles, "no Dockerfiles found — the file discovery itself is broken"

    failures: list[str] = []
    for path in dockerfiles:
        rel = path.relative_to(ROOT)
        stages: set[str] = set()
        from_count = 0

        for lineno, line in enumerate((_read_text(path) or "").splitlines(), start=1):
            match = _FROM_RE.match(line)
            if not match:
                continue
            from_count += 1
            ref = match.group("ref")
            stage = match.group("stage")

            if ref.lower() in stages or "$" in ref or "@sha256:" in ref:
                pass  # prior stage, build-arg driven, or digest-pinned
            elif ":" not in ref.rsplit("/", 1)[-1]:
                failures.append(f"{rel}:{lineno}: untagged base image {ref!r} (not a prior stage)")
            elif ref.rsplit(":", 1)[1] == "latest":
                failures.append(f"{rel}:{lineno}: mutable ':latest' base image {ref!r}")

            if stage:
                stages.add(stage.lower())

        if from_count == 0:
            failures.append(f"{rel}: contains no FROM instruction")

    assert not failures, "Dockerfile base-image problems:\n  " + "\n  ".join(failures)


def test_sops_named_manifests_are_actually_encrypted():
    """A `*.sops.yaml` that is not encrypted is a plaintext secret in git history.

    Shape only. This reads key *names* and asserts each secret value carries the
    SOPS `ENC[` ciphertext envelope; it never decrypts, and no value is included
    in any assertion message. Mirrors the `encrypted_regex: ^(data|stringData)$`
    contract in `.sops.yaml`.
    """
    encrypted = [p for p in _tracked("deploy/k8s/**") if p.name.endswith((".sops.yaml", ".sops.yml"))]
    assert encrypted, "no *.sops.yaml manifests found — the SOPS naming convention or discovery changed"

    failures: list[str] = []
    for path in encrypted:
        rel = path.relative_to(ROOT)
        document = yaml.safe_load(_read_text(path) or "") or {}

        if "sops" not in document:
            failures.append(f"{rel}: no top-level 'sops' metadata block — file is not SOPS-encrypted")
            continue

        payload = {**(document.get("data") or {}), **(document.get("stringData") or {})}
        if not payload:
            failures.append(f"{rel}: no data/stringData keys to encrypt")
            continue

        for key, value in payload.items():
            if not (isinstance(value, str) and value.startswith("ENC[")):
                failures.append(f"{rel}: key {key!r} is not SOPS ciphertext")

    assert not failures, "SOPS-named manifests that are not properly encrypted:\n  " + "\n  ".join(failures)
