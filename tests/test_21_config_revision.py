"""#587 / audit §8.10: the GitOps-owned config-revision rollout trigger.

``verdify-config`` is consumed via ``envFrom`` by every long-running app
workload, so a ConfigMap edit alone does NOT restart any pod. The
``verdify.io/config-revision`` pod-template annotation (maintained by
``scripts/gen-config-revision.sh``) is the deterministic rollout trigger:
this suite fails CI whenever a runtime ConfigMap source (base ConfigMap,
overlay verdify-config patch, v2 orchestrator ConfigMap, or mounted planner
gather-script ConfigMap) changes without the annotation bump.
"""

from __future__ import annotations

import re
import shutil
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
    "deploy/k8s/components/experiment-v2-orchestrator/workloads.yaml",
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
    revisions: dict[str, tuple[str, ...]] = {}
    for rel in ANNOTATED:
        text = (REPO / rel).read_text()
        matches = tuple(ANNOTATION_RE.findall(text))
        assert matches, f"{rel} is missing the verdify.io/config-revision annotation"
        revisions[rel] = matches
    flattened = {revision for matches in revisions.values() for revision in matches}
    assert len(flattened) == 1, f"config-revision annotations diverged across pod templates: {revisions}"


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


def test_hash_input_covers_the_orchestrator_component_config(tmp_path) -> None:
    """Selector/plan configuration must roll all three orchestrator pods."""
    checkout = tmp_path / "checkout"
    copied_script = checkout / "scripts/gen-config-revision.sh"
    copied_script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, copied_script)
    for source in (
        "deploy/k8s/base/configmap.yaml",
        "deploy/k8s/components/experiment-v2-orchestrator/configmap.yaml",
        "deploy/k8s/components/ingestor-gather-script/gather-script-configmap.yaml",
    ):
        destination = checkout / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / source, destination)
    shutil.copytree(REPO / "deploy/k8s/overlays", checkout / "deploy/k8s/overlays")

    def revision(*scope: str) -> str:
        proc = subprocess.run(
            ["bash", str(copied_script), "--print", *scope],
            cwd=checkout,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return proc.stdout.strip()

    before = {"canonical": revision(), "prod": revision("prod")}
    component = checkout / "deploy/k8s/components/experiment-v2-orchestrator/configmap.yaml"
    component.write_text(component.read_text() + "\n# rollout-probe\n")
    after = {"canonical": revision(), "prod": revision("prod")}

    assert before["canonical"] != after["canonical"]
    assert before["prod"] != after["prod"]


def test_hash_input_covers_the_mounted_gather_script_config(tmp_path) -> None:
    """A subPath-mounted gather script needs an explicit ingestor rollout."""
    checkout = tmp_path / "checkout"
    copied_script = checkout / "scripts/gen-config-revision.sh"
    copied_script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, copied_script)
    for source in (
        "deploy/k8s/base/configmap.yaml",
        "deploy/k8s/components/experiment-v2-orchestrator/configmap.yaml",
        "deploy/k8s/components/ingestor-gather-script/gather-script-configmap.yaml",
    ):
        destination = checkout / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / source, destination)
    shutil.copytree(REPO / "deploy/k8s/overlays", checkout / "deploy/k8s/overlays")

    before = subprocess.run(
        ["bash", str(copied_script), "--print"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    gather = checkout / "deploy/k8s/components/ingestor-gather-script/gather-script-configmap.yaml"
    gather.write_text(gather.read_text() + "\n# rollout-probe\n")
    after = subprocess.run(
        ["bash", str(copied_script), "--print"],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert before != after


def test_write_repairs_a_later_annotation_when_the_first_is_current(tmp_path) -> None:
    """A multi-Deployment file must not hide drift behind its first template."""
    checkout = tmp_path / "checkout"
    shutil.copytree(REPO / "scripts", checkout / "scripts")
    shutil.copytree(REPO / "deploy", checkout / "deploy")
    workload = checkout / "deploy/k8s/components/experiment-v2-orchestrator/workloads.yaml"
    text = workload.read_text()
    current = ANNOTATION_RE.search(text).group(1)
    first_end = ANNOTATION_RE.search(text).end()
    drifted = text[:first_end] + text[first_end:].replace(current, "000000000000", 1)
    workload.write_text(drifted)

    proc = subprocess.run(
        ["bash", str(checkout / "scripts/gen-config-revision.sh")],
        cwd=checkout,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert set(ANNOTATION_RE.findall(workload.read_text())) == {current}


def test_overlay_scoped_invocation_never_rewrites() -> None:
    proc = _run("prod")
    assert proc.returncode != 0, (
        "overlay-scoped write must be refused: the committed annotation is always the canonical all-overlay hash"
    )
