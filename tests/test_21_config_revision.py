"""#587 / audit §8.10: the GitOps-owned config-revision rollout trigger.

``verdify-config`` is consumed via ``envFrom`` by every long-running app
workload, so a ConfigMap edit alone does NOT restart any pod. The
``verdify.io/config-revision`` pod-template annotation (maintained by
``scripts/gen-config-revision.sh``) is the deterministic rollout trigger:
this suite fails CI whenever a verdify-config source (base ConfigMap or any
overlay verdify-config patch) changes without the annotation bump.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/gen-config-revision.sh"

# The long-running envFrom consumers carrying the annotation. Jobs/CronJobs
# (migration-job, lab-publisher, ha-gap-backfill) read the ConfigMap fresh per
# run and need no rollout trigger.
ANNOTATED = [
    "deploy/k8s/base/api-deployment.yaml",
    "deploy/k8s/base/mcp-deployment.yaml",
    "deploy/k8s/base/ingestor-deployment.yaml",
    "deploy/k8s/components/planner/planner-deployment.yaml",
    "deploy/k8s/components/setpoint-server/setpoint-server.yaml",
]

ANNOTATION_RE = re.compile(r'verdify\.io/config-revision: "([0-9a-f]{12})"')


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
    )


def test_committed_annotations_match_recomputed_hash() -> None:
    """The CI gate: a verdify-config edit without a revision bump fails here.

    Fix: run ``scripts/gen-config-revision.sh`` and commit the annotation
    updates together with the ConfigMap change.
    """
    proc = _run("--check")
    assert proc.returncode == 0, f"config-revision annotations are stale:\n{proc.stdout}{proc.stderr}"


def test_every_envfrom_consumer_carries_the_annotation() -> None:
    revisions = {}
    for rel in ANNOTATED:
        text = (REPO / rel).read_text()
        match = ANNOTATION_RE.search(text)
        assert match, f"{rel} is missing the verdify.io/config-revision annotation"
        revisions[rel] = match.group(1)
    assert len(set(revisions.values())) == 1, f"config-revision annotations diverged across pod templates: {revisions}"


def test_print_is_deterministic_and_matches_committed_value() -> None:
    first = _run("--print")
    second = _run("--print")
    assert first.returncode == 0 and second.returncode == 0
    assert first.stdout == second.stdout, "gen-config-revision.sh is not deterministic"
    committed = ANNOTATION_RE.search((REPO / ANNOTATED[0]).read_text()).group(1)
    assert first.stdout.strip() == committed


def test_hash_input_covers_base_and_overlay_config_patches() -> None:
    """The canonical hash must include the overlay verdify-config patches —
    a §8.10 rollout step flips flags in overlays/prod, and that edit must
    bump the revision. Scoped (single-overlay) hashes therefore differ from
    each other and from the canonical value."""
    canonical = _run("--print").stdout.strip()
    prod = _run("--print", "prod").stdout.strip()
    prod_dark = _run("--print", "prod-dark").stdout.strip()
    assert len({canonical, prod, prod_dark}) == 3, (
        "expected distinct canonical / prod-scoped / prod-dark-scoped hashes; "
        "overlay verdify-config patches are not reaching the hash input"
    )


def test_overlay_scoped_invocation_never_rewrites() -> None:
    proc = _run("prod")
    assert proc.returncode != 0, (
        "overlay-scoped write must be refused: the committed annotation is always the canonical all-overlay hash"
    )
