"""Guard: this repo runs zero GitHub-hosted CI compute.

GitHub Actions execution was removed on 2026-07-11 (operator directive: no
external CI dependency). The pre-merge gate is `scripts/ci-local.sh`, run
in-cluster by the `verdify-platform-ci` Argo Workflow, which reports the
required check `Verdify Platform / Argo PR CI` via the commit-status API.

`.github/workflows/` is therefore expected to stay EMPTY on `main`. The risk
this guard exists to catch is reintroduction: ~40 stale branches still carry
the retired `ubuntu-latest` workflows, and a `pull_request` from any of them
merges those files into the merge ref, which is what Actions evaluates. If a
workflow ever lands here again it must be self-hosted, least-privilege, and
SHA-pinned rather than silently restoring hosted-runner execution.

Ledger and rollback: docs/ci/zero-paid-runner-ledger.md
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

# GitHub-hosted runner labels. On a public repo these are free, but they are
# still *hosted* execution: outside the cluster, outside the zot/ArgoCD
# pipeline, and billable the moment the repo turns private.
HOSTED_LABEL_RE = re.compile(
    r"^(ubuntu|windows|macos)-(latest|\d[\w.]*)(-\d+core|-arm|-xl|-large)?$",
    re.IGNORECASE,
)

# Actions published by GitHub itself are exempt from SHA pinning; anything
# else is third-party and must be pinned to a reviewed commit SHA.
FIRST_PARTY_ACTION_PREFIXES = ("actions/", "github/")
SHA_PINNED_RE = re.compile(r"@[0-9a-f]{40}$")


def _workflow_files() -> list[Path]:
    if not WORKFLOW_DIR.is_dir():
        return []
    return sorted(p for p in WORKFLOW_DIR.iterdir() if p.suffix in {".yml", ".yaml"})


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
        return [labels] if isinstance(labels, str) else list(labels)
    return []


def _steps(spec: dict) -> list[dict]:
    return [step for step in (spec.get("steps") or []) if isinstance(step, dict)]


def test_no_workflow_uses_a_github_hosted_runner():
    """Every job must run on self-hosted compute — never ubuntu/windows/macos-*."""
    offenders: list[str] = []
    for path in _workflow_files():
        document = yaml.safe_load(path.read_text()) or {}
        for job_name, spec in _jobs(document).items():
            for label in _runs_on_labels(spec):
                # `${{ ... }}` expressions can resolve to a hosted label at run
                # time; require a literal, auditable self-hosted label instead.
                if "${{" in label or HOSTED_LABEL_RE.match(label.strip()):
                    offenders.append(f"{path.name}:{job_name} runs-on={label!r}")
    assert not offenders, (
        "GitHub-hosted runner labels reintroduced — this repo runs zero hosted CI compute.\n"
        "Use a platform-approved self-hosted/ARC label, or retire the workflow.\n"
        "See docs/ci/zero-paid-runner-ledger.md.\n  " + "\n  ".join(offenders)
    )


def test_every_workflow_declares_explicit_minimal_permissions():
    """No workflow may inherit the default (broad) GITHUB_TOKEN scope."""
    missing: list[str] = []
    for path in _workflow_files():
        document = yaml.safe_load(path.read_text()) or {}
        if "permissions" in document:
            continue
        # A top-level block is the norm; per-job blocks are acceptable only if
        # EVERY job declares one, otherwise some job silently inherits default.
        jobs = _jobs(document)
        if not jobs or any("permissions" not in spec for spec in jobs.values()):
            missing.append(path.name)
    assert not missing, (
        "Workflows without explicit `permissions:` inherit a broad GITHUB_TOKEN.\n"
        "Declare the minimal scope (usually `contents: read`).\n  " + "\n  ".join(missing)
    )


def test_third_party_actions_are_pinned_to_commit_shas():
    """Tag/branch refs on third-party Actions are mutable supply-chain surface."""
    unpinned: list[str] = []
    for path in _workflow_files():
        document = yaml.safe_load(path.read_text()) or {}
        for job_name, spec in _jobs(document).items():
            for step in _steps(spec):
                uses = step.get("uses")
                if not isinstance(uses, str) or uses.startswith(("./", "docker://")):
                    continue
                if uses.startswith(FIRST_PARTY_ACTION_PREFIXES):
                    continue
                if not SHA_PINNED_RE.search(uses):
                    unpinned.append(f"{path.name}:{job_name} uses={uses!r}")
    assert not unpinned, "Third-party Actions must be pinned to a reviewed 40-char commit SHA.\n  " + "\n  ".join(
        unpinned
    )


def test_workflow_directory_is_still_empty_on_this_branch():
    """Tripwire: records the expected steady state (zero workflow files).

    This is the condition the 2026-07-11 cutover established. If a workflow is
    deliberately reintroduced, update docs/ci/zero-paid-runner-ledger.md with
    its disposition and target runner profile, then relax this assertion — the
    three guards above keep applying.
    """
    present = [p.name for p in _workflow_files()]
    assert not present, (
        "GitHub Actions workflows reappeared on this branch: "
        f"{present}. Give each an explicit disposition in "
        "docs/ci/zero-paid-runner-ledger.md before merging."
    )
