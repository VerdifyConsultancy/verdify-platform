"""Guard: GitHub Actions in this repo run only on the estate's self-hosted ARC sets.

Issue #1397 moves CI from the in-cluster Argo pipeline to GitHub Actions on
self-hosted ARC runners (estate CI rules R1-R3, jvallery/estate-ci). This repo
is public, so the guard keeps every workflow job on one of this repository's
own scale sets and never on GitHub-hosted compute:

- R1: a job either names exactly one literal `verdify-platform-*` ARC label in
  `runs-on`, or calls an estate-ci reusable workflow whose `runner:` input (when
  given) is such a label; the reusable workflows default to `<repo>-ci`.
- R2: `uses:` is only actions/checkout or jvallery/estate-ci, pinned to a full
  commit SHA; no pull_request_target / workflow_run triggers.
- Every workflow declares explicit `permissions:`.

`estate-ci lint --strict` (the `ci / lint` check) enforces the same rules from
the estate side; this test keeps them in the repository's own gate.
Ledger: docs/ci/zero-paid-runner-ledger.md.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

# This repository's ARC scale sets: <repo>-ci, <repo>-ci-<variant>, <repo>-build,
# <repo>-buildcheck, <repo>-pg (estate-ci runners.yaml naming).
ARC_LABEL = re.compile(r"^verdify-platform-(ci(-[a-z0-9]{1,12})?|build|buildcheck|pg)$")

# GitHub-hosted labels evolve, but their OS-family prefixes are stable. Treat
# every current or future ubuntu/windows/macos family label as hosted, plus the
# generic labels that could match a hosted or foreign runner.
HOSTED_LABEL_PREFIXES = ("ubuntu-", "windows-", "macos-")
GENERIC_LABELS = {"self-hosted", "linux", "x64", "arm64", "arm", "windows", "macos"}

SHA = r"[0-9a-f]{40}"
ALLOWED_USES = (
    re.compile(rf"^actions/checkout@{SHA}$"),
    re.compile(rf"^jvallery/estate-ci/\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml@{SHA}$"),
)
FORBIDDEN_TRIGGERS = {"pull_request_target", "workflow_run"}


def _workflow_files() -> list[Path]:
    if not WORKFLOW_DIR.is_dir():
        return []
    return sorted(p for p in WORKFLOW_DIR.iterdir() if p.suffix in {".yml", ".yaml"})


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def _jobs(document: dict) -> dict[str, dict]:
    jobs = document.get("jobs") or {}
    return {name: spec for name, spec in jobs.items() if isinstance(spec, dict)}


def _runs_on_labels(spec: dict) -> list[str]:
    runs_on = spec.get("runs-on")
    if runs_on is None:
        return []
    if isinstance(runs_on, str):
        return [runs_on]
    if isinstance(runs_on, list):
        return [label for label in runs_on if isinstance(label, str)]
    if isinstance(runs_on, dict):  # { group: ..., labels: [...] }
        labels = runs_on.get("labels") or []
        if isinstance(labels, str):
            return [labels]
        if isinstance(labels, list):
            return [label for label in labels if isinstance(label, str)]
    return []


def _is_github_hosted_label(label: str) -> bool:
    value = label.strip().lower()
    return value.startswith(HOSTED_LABEL_PREFIXES) or value in GENERIC_LABELS


def _is_arc_label(label: str) -> bool:
    return bool(ARC_LABEL.fullmatch(label.strip()))


def _triggers(document: dict) -> list[str]:
    # PyYAML reads the bare key `on` as boolean True.
    on = document.get("on", document.get(True))
    if isinstance(on, dict):
        return [str(key) for key in on]
    if isinstance(on, list):
        return [str(key) for key in on]
    return [str(on)] if on else []


def _job_problems(path: Path, job_name: str, spec: dict) -> list[str]:
    where = f"{path.name}:{job_name}"
    if "uses" in spec:
        problems = []
        uses = spec["uses"]
        if not any(p.match(str(uses)) for p in ALLOWED_USES[1:]):
            problems.append(f"{where} calls {uses!r}: only jvallery/estate-ci reusable workflows at a full SHA")
        runner = (spec.get("with") or {}).get("runner")
        if runner is not None and not _is_arc_label(str(runner)):
            problems.append(f"{where} runner={runner!r} is not a verdify-platform ARC set")
        return problems
    labels = _runs_on_labels(spec)
    if len(labels) != 1:
        return [f"{where} runs-on={spec.get('runs-on')!r}: exactly one literal ARC label required"]
    if not _is_arc_label(labels[0]):
        return [f"{where} runs-on={labels[0]!r} is not a verdify-platform ARC set"]
    return []


def test_workflows_exist_and_run_only_on_arc_sets():
    files = _workflow_files()
    assert files, "issue #1397: .github/workflows/ci.yml is the repository's CI"
    problems = [
        p for path in files for name, spec in _jobs(_load(path)).items() for p in _job_problems(path, name, spec)
    ]
    assert not problems, "Workflows must run only on this repository's ARC scale sets:\n  " + "\n  ".join(problems)


def test_no_github_hosted_or_generic_label_anywhere():
    """A hosted label must not appear even inside an expression or a runner input."""
    offenders = []
    for path in _workflow_files():
        for number, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            match = re.search(r"\b(?:runs-on|runner):\s*(.+)$", code)
            if not match:
                continue
            for token in re.findall(r"[A-Za-z0-9_.-]+", match.group(1)):
                if _is_github_hosted_label(token):
                    offenders.append(f"{path.name}:{number}: {token}")
    assert not offenders, "GitHub-hosted runner labels are not allowed:\n  " + "\n  ".join(offenders)


def test_step_actions_are_checkout_or_estate_ci_pinned_to_a_sha():
    bad = []
    for path in _workflow_files():
        for job_name, spec in _jobs(_load(path)).items():
            for step in spec.get("steps") or []:
                uses = step.get("uses") if isinstance(step, dict) else None
                if uses is not None and not any(p.match(str(uses)) for p in ALLOWED_USES):
                    bad.append(f"{path.name}:{job_name} uses={uses!r}")
    assert not bad, "Only actions/checkout or jvallery/estate-ci at a full commit SHA (R2):\n  " + "\n  ".join(bad)


def test_no_privileged_triggers():
    bad = [
        f"{path.name}: {trigger}"
        for path in _workflow_files()
        for trigger in _triggers(_load(path))
        if trigger in FORBIDDEN_TRIGGERS
    ]
    assert not bad, "pull_request_target and workflow_run run untrusted code with base privileges:\n  " + "\n  ".join(
        bad
    )


def test_every_workflow_declares_explicit_minimal_permissions():
    """No workflow may inherit the default (broad) GITHUB_TOKEN scope."""
    missing = []
    for path in _workflow_files():
        document = _load(path)
        if "permissions" in document:
            continue
        jobs = _jobs(document)
        if not jobs or any("permissions" not in spec for spec in jobs.values()):
            missing.append(path.name)
    assert not missing, "Workflows without explicit `permissions:` inherit a broad GITHUB_TOKEN:\n  " + "\n  ".join(
        missing
    )


def test_label_classifiers():
    for label in ("ubuntu-latest", "ubuntu-slim", "macos-15-intel", "windows-11-vs2026-arm", "self-hosted", "linux"):
        assert _is_github_hosted_label(label)
        assert not _is_arc_label(label)
    for label in (
        "verdify-platform-ci",
        "verdify-platform-ci-tools",
        "verdify-platform-ci-dashboard",
        "verdify-platform-build",
    ):
        assert _is_arc_label(label)
        assert not _is_github_hosted_label(label)
    for label in ("verdify-ci", "agent-fleet-ci", "weather-ci", "verdify-platform-ci-${{ x }}"):
        assert not _is_arc_label(label)


def test_job_checks_reject_hosted_and_foreign_runners(tmp_path):
    path = tmp_path / "x.yml"
    assert _job_problems(path, "a", {"runs-on": "ubuntu-latest"})
    assert _job_problems(path, "b", {"runs-on": ["self-hosted", "verdify-platform-ci"]})
    assert _job_problems(path, "c", {"runs-on": "${{ matrix.os }}"})
    assert _job_problems(path, "d", {"uses": "jvallery/estate-ci/.github/workflows/checks.yml@main"})
    assert _job_problems(
        path,
        "e",
        {"uses": "jvallery/estate-ci/.github/workflows/checks.yml@" + "a" * 40, "with": {"runner": "ubuntu-24.04"}},
    )
    assert not _job_problems(path, "f", {"runs-on": "verdify-platform-ci"})
    assert not _job_problems(path, "g", {"uses": "jvallery/estate-ci/.github/workflows/checks.yml@" + "a" * 40})
