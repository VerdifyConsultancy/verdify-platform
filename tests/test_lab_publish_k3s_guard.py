"""Failure-isolation tests for the in-cluster Lab publication wrapper."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from yaml.constructor import ConstructorError

from verdify_public import output_policy as public_policy

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "lab-publish-k3s.sh"
PREPARE_CACHE = REPO_ROOT / "scripts" / "prepare-lab-cache.sh"


def _prepare_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "VERDIFY_PUBLIC_OUTPUT_GUARD": str(REPO_ROOT / "scripts" / "check-public-output.py"),
            "VERDIFY_PUBLIC_OUTPUT_PYTHON": sys.executable,
        }
    )
    env.update(extra or {})
    return env


def _read_layout_attestation(path: Path) -> tuple[dict[str, object], str]:
    raw = path.read_text(encoding="utf-8")
    value = json.loads(raw)
    assert set(value) == {"contract", "root_identity", "schema_version", "tree_digest"}
    assert value["contract"] == "verdify.public-output-layout-attestation"
    assert value["schema_version"] == 1
    assert all(
        isinstance(value[key], str) and value[key].startswith("sha256:") and len(value[key]) == len("sha256:") + 64
        for key in ("root_identity", "tree_digest")
    )
    assert raw == json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    return value, raw


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently applying last-key-wins."""


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_unique_yaml(path: Path) -> list[dict]:
    return list(yaml.load_all(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader))


def _resource(documents: list[dict], kind: str, name: str) -> dict:
    return next(
        document
        for document in documents
        if document.get("kind") == kind and document.get("metadata", {}).get("name") == name
    )


# Must stay in step with LAB_IDENTITY_ROUTES in scripts/prepare-lab-cache.sh.
LAB_IDENTITY_ROUTES = (
    "plans/index.html",
    "data/forecast/index.html",
    "start/index.html",
    "greenhouse/index.html",
)


def _write_lab_site_tree(root: Path, homepage: str = "baked fallback") -> None:
    """Smallest tree prepare-lab-cache will accept as a Verdify Lab build.

    A bare index.html is exactly the shape of the Quartz-stock-docs build that
    the verdify-lab image baked and that reached lab.verdify.ai on 2026-07-26,
    so is_lab_site_tree() requires content-derived routes plus the dated plan
    archive that every real build emits.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(homepage, encoding="utf-8")
    for route in LAB_IDENTITY_ROUTES:
        target = root / route
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"verdify {route}", encoding="utf-8")
    (root / "plans" / "2026-07-25.html").write_text("plan archive page", encoding="utf-8")


def _write_stock_quartz_tree(root: Path) -> None:
    """An upstream `npx quartz build` with no Verdify content tree.

    quartz.config.ts still stamps og:site_name "Verdify Lab" onto this build,
    and advanced/ features/ plugins/ exist in both trees, so branding and
    directory names cannot discriminate — only content-derived routes can.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        '<title>Welcome to Quartz 4 — Verdify Lab</title><meta name="og:site_name" content="Verdify Lab"/>',
        encoding="utf-8",
    )
    for page in ("philosophy.html", "authoring-content.html", "migrating-from-Quartz-3.html"):
        (root / page).write_text("upstream quartz docs", encoding="utf-8")
    for directory in ("advanced", "features", "plugins", "static", "tags"):
        (root / directory).mkdir(exist_ok=True)
        (root / directory / "index.html").write_text("quartz section", encoding="utf-8")


def test_manifest_loader_rejects_duplicate_keys():
    with pytest.raises(ConstructorError, match="duplicate key 'initContainers'"):
        yaml.load(
            "spec:\n  initContainers: []\n  initContainers: []\n",
            Loader=_UniqueKeyLoader,  # noqa: S506 - stricter SafeLoader subclass
        )


@pytest.fixture
def harness(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    aws_log = tmp_path / "aws.log"
    delta_log = tmp_path / "delta.log"
    block_signal = tmp_path / "sync.blocked"
    block_release = tmp_path / "sync.release"

    aws = bin_dir / "aws"
    aws.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s:%s\n' "${FIXTURE_RUN_ID:-default}" "$*" >> "$FIXTURE_AWS_LOG"
if [[ "$1 $2" == "s3 ls" ]]; then
  echo '2026-07-11 00:00:00          1 index.md'
  exit 0
fi
if [[ "$1 $2" == "s3 sync" ]]; then
  if [[ "${FIXTURE_BLOCK_SYNC:-0}" == '1' ]]; then
    touch "$FIXTURE_BLOCK_SIGNAL"
    while [[ ! -f "$FIXTURE_BLOCK_RELEASE" ]]; do sleep 0.02; done
  fi
  mkdir -p "$4"
  printf '# fixture\n' > "$4/index.md"
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    aws.chmod(0o755)

    fake_python = bin_dir / "python"
    fake_python.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
label=''
while [[ $# -gt 0 ]]; do
  if [[ "$1" == '--label' ]]; then label="$2"; shift 2; else shift; fi
done
printf '%s\n' "$label" >> "$FIXTURE_DELTA_LOG"
if [[ "${FIXTURE_FAIL_STATE_SYNC:-0}" == '1' && "$label" == 'state' ]]; then exit 9; fi
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    publish = tmp_path / "publish"
    publish.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf 'fixture publish\n' >> "$LAB_WORK_ROOT/state/publish.log"
printf '%s\n' "$VERDIFY_PUBLIC_CONTENT_ROOT" > "$LAB_WORK_ROOT/state/public-content-root"
exit "${FIXTURE_PUBLISH_RC:-0}"
""",
        encoding="utf-8",
    )
    publish.chmod(0o755)

    work = tmp_path / "work"
    site_runtime = tmp_path / "site-runtime"
    site_runtime.mkdir()
    links = tmp_path / "links"

    def make_env(
        *,
        publish_rc: int = 0,
        fail_state_sync: bool = False,
        reason: str = "k3s-publisher",
        run_id: str = "default",
        block_sync: bool = False,
    ) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "PYTHON": str(fake_python),
                "LAB_S3_BUCKET": "fixture-bucket",
                "LAB_WORK_ROOT": str(work),
                "LAB_SITE_RUNTIME": str(site_runtime),
                "LAB_PUBLISH_SCRIPT": str(publish),
                "LAB_SITE_CONTENT_LINK": str(links / "site" / "content"),
                "LAB_STATE_LINK": str(links / "state"),
                "LAB_REPO_LINK": str(links / "repo"),
                "LAB_VAULT_LINK": str(links / "vault" / "website"),
                "FIXTURE_AWS_LOG": str(aws_log),
                "FIXTURE_DELTA_LOG": str(delta_log),
                "FIXTURE_PUBLISH_RC": str(publish_rc),
                "FIXTURE_FAIL_STATE_SYNC": "1" if fail_state_sync else "0",
                "FIXTURE_RUN_ID": run_id,
                "FIXTURE_BLOCK_SYNC": "1" if block_sync else "0",
                "FIXTURE_BLOCK_SIGNAL": str(block_signal),
                "FIXTURE_BLOCK_RELEASE": str(block_release),
                "LAB_PUBLISH_REASON": reason,
                "VERDIFY_SCRIPT_ROOT": str(REPO_ROOT / "scripts"),
            }
        )
        return env

    def run(
        *,
        publish_rc: int = 0,
        fail_state_sync: bool = False,
        reason: str = "k3s-publisher",
        run_id: str = "default",
    ):
        env = make_env(
            publish_rc=publish_rc,
            fail_state_sync=fail_state_sync,
            reason=reason,
            run_id=run_id,
        )
        proc = subprocess.run(
            ["bash", str(SCRIPT), "2026-07-11"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        deltas = delta_log.read_text(encoding="utf-8").splitlines() if delta_log.exists() else []
        aws_calls = aws_log.read_text(encoding="utf-8").splitlines() if aws_log.exists() else []
        return proc, deltas, aws_calls

    def start(*, reason: str = "k3s-publisher", run_id: str = "default", block_sync: bool = False):
        return subprocess.Popen(
            ["bash", str(SCRIPT), "2026-07-11"],
            env=make_env(reason=reason, run_id=run_id, block_sync=block_sync),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    run.start = start
    run.block_signal = block_signal
    run.block_release = block_release
    run.work = work
    run.site_runtime = site_runtime
    return run


def test_failed_candidate_syncs_state_but_never_content_or_public(harness):
    proc, deltas, aws_calls = harness(publish_rc=23)

    assert proc.returncode == 23
    assert deltas == ["state"]
    assert "syncing state only" in proc.stderr
    assert any("--delete --exact-timestamps" in call for call in aws_calls)


def test_state_sync_failure_preserves_original_publish_status(harness):
    proc, deltas, _ = harness(publish_rc=42, fail_state_sync=True)

    assert proc.returncode == 42
    assert deltas == ["state"]
    assert "State sync also failed" in proc.stderr


def test_successful_candidate_syncs_content_public_then_state(harness):
    proc, deltas, _ = harness()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert deltas == ["content", "public", "state"]
    exported_root = Path((harness.work / "state" / "public-content-root").read_text(encoding="utf-8").strip())
    assert exported_root == harness.work / "content"
    assert exported_root.is_dir()
    assert not exported_root.is_symlink()
    runtime_link = harness.site_runtime / "content"
    assert runtime_link.is_symlink()
    assert runtime_link.resolve() == exported_root.resolve()


def test_wrapper_logs_allowlisted_reason_class_without_raw_text_or_digest(harness):
    encoded_identifier = "%70%72%69%76%61%74%65%2D%67%72%65%65%6E%68%6F%75%73%65"
    raw_reason = f"private-greenhouse-identifier\nforged-log-entry {encoded_identifier}"
    digest_prefix = hashlib.sha256(raw_reason.encode()).hexdigest()[:12]

    proc, _, _ = harness(reason=raw_reason, run_id="reason-test")
    output = proc.stdout + proc.stderr

    assert proc.returncode == 0, output
    assert "private-greenhouse-identifier" not in output
    assert "forged-log-entry" not in output
    assert encoded_identifier not in output
    assert digest_prefix not in output
    assert "reason_class=custom" in output


def test_outer_lock_blocks_loser_before_candidate_sync(harness):
    winner = harness.start(run_id="winner", block_sync=True)
    try:
        deadline = time.monotonic() + 10
        while not harness.block_signal.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert harness.block_signal.exists(), "winner never reached the blocked source sync"

        loser, _, aws_calls = harness(run_id="loser")

        assert loser.returncode == 75, loser.stdout + loser.stderr
        assert "k3s lab publish already running" in loser.stderr
        assert not any(call.startswith("loser:") for call in aws_calls)
        assert not (harness.work / "content" / "index.md").exists()
    finally:
        harness.block_release.touch()
    winner_stdout, winner_stderr = winner.communicate(timeout=20)
    assert winner.returncode == 0, winner_stdout + winner_stderr


def test_k3s_build_lock_contention_is_a_publish_failure():
    wrapper = SCRIPT.read_text(encoding="utf-8")
    rebuild = (REPO_ROOT / "scripts" / "rebuild-site.sh").read_text(encoding="utf-8")

    assert 'VERDIFY_SITE_BUILD_LOCKED_RC="${VERDIFY_SITE_BUILD_LOCKED_RC:-75}"' in wrapper
    assert 'WRAPPER_LOCKED_RC="${LAB_PUBLISH_WRAPPER_LOCKED_RC:-75}"' in wrapper
    assert "LOCKED_RC=${VERDIFY_SITE_BUILD_LOCKED_RC:-0}" in rebuild
    assert 'exit "$LOCKED_RC"' in rebuild


def test_private_work_root_is_descriptor_normalized_before_publish(harness):
    harness.work.mkdir(mode=0o770)
    harness.work.chmod(0o770)

    proc, deltas, aws_calls = harness()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert harness.work.stat().st_mode & 0o777 == 0o700
    assert deltas
    assert aws_calls


def test_cache_initializer_preserves_legacy_last_good_and_private_layout(tmp_path: Path):
    work = tmp_path / "work"
    legacy = work / "public"
    bootstrap = tmp_path / "bootstrap"
    _write_lab_site_tree(legacy, homepage="legacy last-good")
    _write_lab_site_tree(bootstrap)
    work.chmod(0o770)  # Models the fsGroup-writable PVC root.

    proc = subprocess.run(
        [
            "bash",
            str(PREPARE_CACHE),
            "--root",
            str(work / "publisher"),
            "--legacy",
            str(legacy),
            "--bootstrap",
            str(bootstrap),
        ],
        env=_prepare_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    private_root = work / "publisher"
    assert private_root.stat().st_uid == os.getuid()
    assert private_root.stat().st_mode & 0o777 == 0o700
    assert (private_root / "public").stat().st_mode & 0o777 == 0o755
    assert (private_root / "public" / "index.html").read_text(encoding="utf-8") == "legacy last-good"
    _read_layout_attestation(private_root / ".layout-v2-scanned-ready")
    assert not list(private_root.glob(".layout-v2-init.*"))


@pytest.mark.parametrize("hazard", ["content", "entry-symlink", "root-symlink"])
def test_cache_initializer_never_promotes_unsafe_old_recovery_residue(tmp_path: Path, hazard: str):
    private_root = tmp_path / "work" / "publisher"
    old = private_root / ".layout-v1-old"
    excluded = next(iter(public_policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    if hazard == "root-symlink":
        outside = tmp_path / "outside-old"
        outside.mkdir()
        (outside / "index.html").write_text("outside old", encoding="utf-8")
        private_root.mkdir(parents=True)
        old.symlink_to(outside, target_is_directory=True)
    else:
        old.mkdir(parents=True)
    if hazard == "content":
        (old / "index.html").write_text(f"hidden {excluded}", encoding="utf-8")
    elif hazard == "entry-symlink":
        outside = tmp_path / "outside.html"
        outside.write_text("outside", encoding="utf-8")
        (old / "index.html").symlink_to(outside)

    proc = subprocess.run(
        ["bash", str(PREPARE_CACHE), "--root", str(private_root)],
        env=_prepare_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert output.strip() == "Lab cache recovery residue validation failed"
    assert excluded not in output.casefold()
    assert old.is_dir()
    assert old.is_symlink() is (hazard == "root-symlink")
    assert not (private_root / "public").exists()


def test_cache_initializer_never_follows_forged_marker_public_symlink(tmp_path: Path):
    private_root = tmp_path / "work" / "publisher"
    outside = tmp_path / "outside"
    private_root.mkdir(parents=True)
    outside.mkdir()
    (outside / "index.html").write_text("outside must remain private", encoding="utf-8")
    public = private_root / "public"
    public.symlink_to(outside, target_is_directory=True)
    marker = private_root / ".layout-v2-scanned-ready"
    forged = (
        '{"contract":"verdify.public-output-layout-attestation",'
        f'"root_identity":"sha256:{"0" * 64}","schema_version":1,'
        f'"tree_digest":"sha256:{"1" * 64}"}}\n'
    )
    marker.write_text(forged, encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(PREPARE_CACHE), "--root", str(private_root)],
        env=_prepare_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode != 0
    assert (proc.stdout + proc.stderr).strip() == "Lab cache public root is not a directory"
    assert public.is_symlink()
    assert (outside / "index.html").read_text(encoding="utf-8") == "outside must remain private"
    assert marker.read_text(encoding="utf-8") == forged


@pytest.mark.parametrize(
    "hazard",
    [
        "locks-symlink",
        "locks-file",
        "locks-mode",
        "lock-symlink",
        "lock-hardlink",
        "lock-directory",
        "lock-fifo",
        "lock-mode",
    ],
)
def test_cache_lock_paths_fail_closed_without_following_or_mutating_outside_targets(tmp_path: Path, hazard: str):
    private_root = tmp_path / "work" / "publisher"
    locks = private_root / "locks"
    lock_file = locks / "publish-wrapper.lock"
    outside_directory = tmp_path / "outside-locks"
    outside_file = tmp_path / "outside-lock"
    private_root.mkdir(parents=True)
    outside_directory.mkdir(mode=0o750)
    (outside_directory / "sentinel").write_text("outside directory sentinel", encoding="utf-8")
    outside_file.write_text("outside lock sentinel", encoding="utf-8")
    outside_file.chmod(0o640)
    outside_directory_mode = outside_directory.stat().st_mode & 0o777
    outside_file_mode = outside_file.stat().st_mode & 0o777

    if hazard == "locks-symlink":
        locks.symlink_to(outside_directory, target_is_directory=True)
    elif hazard == "locks-file":
        locks.write_text("not a directory", encoding="utf-8")
    else:
        locks.mkdir(mode=0o700)
        if hazard == "locks-mode":
            locks.chmod(0o755)
        elif hazard == "lock-symlink":
            lock_file.symlink_to(outside_file)
        elif hazard == "lock-hardlink":
            os.link(outside_file, lock_file)
        elif hazard == "lock-directory":
            lock_file.mkdir()
        elif hazard == "lock-fifo":
            os.mkfifo(lock_file)
        elif hazard == "lock-mode":
            lock_file.write_text("existing lock bytes", encoding="utf-8")
            lock_file.chmod(0o640)

    proc = subprocess.run(
        ["bash", str(PREPARE_CACHE), "--root", str(private_root)],
        env=_prepare_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert proc.returncode == 2
    assert (proc.stdout + proc.stderr).strip() == "Lab cache lock initialization failed"
    assert (outside_directory / "sentinel").read_text(encoding="utf-8") == "outside directory sentinel"
    assert outside_file.read_text(encoding="utf-8") == "outside lock sentinel"
    assert outside_directory.stat().st_mode & 0o777 == outside_directory_mode
    assert outside_file.stat().st_mode & 0o777 == outside_file_mode
    assert not (private_root / "public").exists()


@pytest.mark.parametrize(
    ("entrypoint", "lock_fd"),
    [(PREPARE_CACHE, 9), (SCRIPT, 8)],
    ids=["initializer", "publisher"],
)
def test_cache_lock_reentry_rejects_forged_unrelated_descriptor_without_mutation(
    tmp_path: Path,
    entrypoint: Path,
    lock_fd: int,
):
    private_root = tmp_path / "work" / "publisher"
    locks = private_root / "locks"
    locks.mkdir(parents=True, mode=0o700)
    private_root.chmod(0o700)
    locks.chmod(0o700)
    named_lock = locks / "publish-wrapper.lock"
    unrelated = tmp_path / "unrelated.lock"
    named_lock.write_text("named lock sentinel", encoding="utf-8")
    unrelated.write_text("unrelated lock sentinel", encoding="utf-8")
    named_lock.chmod(0o600)
    unrelated.chmod(0o600)
    assert unrelated.stat().st_uid == os.getuid()

    command = ["bash", str(entrypoint)]
    if entrypoint == SCRIPT:
        command.append("2026-07-12")
    env = _prepare_environment(
        {
            "FORGED_FD_PATH": str(unrelated),
            "LAB_S3_BUCKET": "fixture-bucket",
            "LAB_WORK_ROOT": str(private_root),
            "VERDIFY_CACHE_LOCK_HELD_FD": str(lock_fd),
            "VERDIFY_CACHE_LOCK_HELPER": str(REPO_ROOT / "scripts" / "prepare-lab-cache-lock.py"),
            "VERDIFY_CACHE_PYTHON": sys.executable,
            "VERDIFY_SCRIPT_ROOT": str(REPO_ROOT / "scripts"),
        }
    )
    launcher = f'exec {lock_fd}<>"$FORGED_FD_PATH"; exec "$@"'

    proc = subprocess.run(
        ["bash", "-c", launcher, "forged-lock-test", *command],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert proc.returncode == 2
    assert (proc.stdout + proc.stderr).strip() == "Lab cache lock initialization failed"
    assert named_lock.read_text(encoding="utf-8") == "named lock sentinel"
    assert unrelated.read_text(encoding="utf-8") == "unrelated lock sentinel"
    assert named_lock.stat().st_mode & 0o777 == 0o600
    assert unrelated.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in private_root.iterdir()) == ["locks"]
    assert sorted(path.name for path in locks.iterdir()) == ["publish-wrapper.lock"]


@pytest.mark.parametrize(
    ("entrypoint", "lock_fd"),
    [(PREPARE_CACHE, 9), (SCRIPT, 8)],
    ids=["initializer", "publisher"],
)
def test_forged_unrelated_descriptor_never_creates_missing_cache_root(
    tmp_path: Path,
    entrypoint: Path,
    lock_fd: int,
):
    private_root = tmp_path / "missing" / "publisher"
    unrelated = tmp_path / "unrelated.lock"
    unrelated.write_text("unrelated lock sentinel", encoding="utf-8")
    unrelated.chmod(0o600)
    command = ["bash", str(entrypoint)]
    if entrypoint == SCRIPT:
        command.append("2026-07-12")
    env = _prepare_environment(
        {
            "FORGED_FD_PATH": str(unrelated),
            "LAB_S3_BUCKET": "fixture-bucket",
            "LAB_WORK_ROOT": str(private_root),
            "VERDIFY_CACHE_LOCK_HELD_FD": str(lock_fd),
            "VERDIFY_CACHE_LOCK_HELPER": str(REPO_ROOT / "scripts" / "prepare-lab-cache-lock.py"),
            "VERDIFY_CACHE_PYTHON": sys.executable,
            "VERDIFY_SCRIPT_ROOT": str(REPO_ROOT / "scripts"),
        }
    )
    launcher = f'exec {lock_fd}<>"$FORGED_FD_PATH"; exec "$@"'

    proc = subprocess.run(
        ["bash", "-c", launcher, "forged-lock-test", *command],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert proc.returncode == 2
    assert (proc.stdout + proc.stderr).strip() == "Lab cache lock initialization failed"
    assert not private_root.exists()
    assert unrelated.read_text(encoding="utf-8") == "unrelated lock sentinel"
    assert unrelated.stat().st_uid == os.getuid()
    assert unrelated.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("entrypoint", [PREPARE_CACHE, SCRIPT], ids=["initializer", "publisher"])
def test_cache_root_intermediate_symlink_preserves_outside_tree(
    tmp_path: Path,
    entrypoint: Path,
):
    declared_parent = tmp_path / "declared"
    outside_parent = tmp_path / "outside"
    outside_root = outside_parent / "publisher"
    declared_parent.mkdir()
    outside_root.mkdir(parents=True, mode=0o750)
    outside_root.chmod(0o750)
    sentinel = outside_root / "sentinel"
    sentinel.write_text("outside tree sentinel", encoding="utf-8")
    sentinel.chmod(0o640)
    (declared_parent / "intermediate").symlink_to(outside_parent, target_is_directory=True)
    declared_root = declared_parent / "intermediate" / "publisher"
    outside_root_mode = outside_root.stat().st_mode & 0o777
    sentinel_mode = sentinel.stat().st_mode & 0o777

    command = ["bash", str(entrypoint)]
    if entrypoint == PREPARE_CACHE:
        command.extend(["--root", str(declared_root)])
    else:
        command.append("2026-07-12")
    env = _prepare_environment(
        {
            "LAB_S3_BUCKET": "fixture-bucket",
            "LAB_WORK_ROOT": str(declared_root),
            "VERDIFY_CACHE_LOCK_HELPER": str(REPO_ROOT / "scripts" / "prepare-lab-cache-lock.py"),
            "VERDIFY_CACHE_PYTHON": sys.executable,
            "VERDIFY_SCRIPT_ROOT": str(REPO_ROOT / "scripts"),
        }
    )

    proc = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert proc.returncode == 2
    assert (proc.stdout + proc.stderr).strip() == "Lab cache lock initialization failed"
    assert sentinel.read_text(encoding="utf-8") == "outside tree sentinel"
    assert outside_root.stat().st_mode & 0o777 == outside_root_mode
    assert sentinel.stat().st_mode & 0o777 == sentinel_mode
    assert sorted(path.name for path in outside_root.iterdir()) == ["sentinel"]


def test_cache_initializer_existing_marker_never_skips_changed_live_validation(tmp_path: Path):
    private_root = tmp_path / "work" / "publisher"
    live = private_root / "public"
    live.mkdir(parents=True)
    (live / "index.html").write_text("clean first version", encoding="utf-8")
    command = ["bash", str(PREPARE_CACHE), "--root", str(private_root)]

    first = subprocess.run(
        command,
        env=_prepare_environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    marker_path = private_root / ".layout-v2-scanned-ready"
    first_marker, first_raw = _read_layout_attestation(marker_path)

    (live / "index.html").write_text("clean second version with different length", encoding="utf-8")
    second = subprocess.run(
        command,
        env=_prepare_environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    second_marker, second_raw = _read_layout_attestation(marker_path)
    assert second_marker["root_identity"] == first_marker["root_identity"]
    assert second_marker["tree_digest"] != first_marker["tree_digest"]
    assert second_raw != first_raw

    excluded = next(iter(public_policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    (live / "index.html").write_text(f"unsafe {excluded}", encoding="utf-8")
    third = subprocess.run(
        command,
        env=_prepare_environment(),
        capture_output=True,
        text=True,
        check=False,
    )
    output = third.stdout + third.stderr
    assert third.returncode != 0
    assert output.strip() == "Lab cache public tree validation failed"
    assert excluded not in output.casefold()
    assert marker_path.read_text(encoding="utf-8") == second_raw


def test_cache_initializer_validates_then_recovers_clean_old_residue(tmp_path: Path):
    private_root = tmp_path / "work" / "publisher"
    old = private_root / ".layout-v1-old"
    old.mkdir(parents=True)
    (old / "index.html").write_text("validated old last-good", encoding="utf-8")

    proc = subprocess.run(
        ["bash", str(PREPARE_CACHE), "--root", str(private_root)],
        env=_prepare_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    live = private_root / "public"
    assert live.is_dir() and not live.is_symlink()
    assert (live / "index.html").read_text(encoding="utf-8") == "validated old last-good"
    assert not old.exists()
    _read_layout_attestation(private_root / ".layout-v2-scanned-ready")


def test_cache_initializer_rejects_unsafe_legacy_before_touching_live(tmp_path: Path):
    work = tmp_path / "work"
    live = work / "publisher" / "public"
    legacy = work / "public"
    live.mkdir(parents=True)
    legacy.mkdir()
    (live / "index.html").write_text("existing last-good", encoding="utf-8")
    excluded = next(iter(public_policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    (legacy / "unsafe.html").write_text(f"hidden {excluded}", encoding="utf-8")
    (legacy / "unsafe-link").symlink_to(legacy / "unsafe.html")

    proc = subprocess.run(
        [
            "bash",
            str(PREPARE_CACHE),
            "--root",
            str(work / "publisher"),
            "--legacy",
            str(legacy),
        ],
        env=_prepare_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert output.strip() == "Lab cache public tree validation failed"
    assert excluded not in output.casefold()
    assert (live / "index.html").read_text(encoding="utf-8") == "existing last-good"
    assert not (work / "publisher" / ".layout-v2-scanned-ready").exists()


@pytest.mark.parametrize("hazard", ["content", "symlink", "hardlink", "special"])
def test_cache_initializer_rejects_unsafe_bootstrap_and_preserves_live(tmp_path: Path, hazard: str):
    work = tmp_path / "work"
    live = work / "publisher" / "public"
    bootstrap = tmp_path / "bootstrap"
    live.mkdir(parents=True)
    bootstrap.mkdir()
    (live / "old-only.html").write_text("existing last-good", encoding="utf-8")
    excluded = next(iter(public_policy.PUBLIC_CROP_EXCLUDE_SLUGS))
    if hazard == "content":
        (bootstrap / "index.html").write_text(f"hidden {excluded}", encoding="utf-8")
    elif hazard == "symlink":
        target = tmp_path / "outside.html"
        target.write_text("outside", encoding="utf-8")
        (bootstrap / "index.html").symlink_to(target)
    elif hazard == "hardlink":
        source = bootstrap / "index.html"
        source.write_text("baked fallback", encoding="utf-8")
        os.link(source, bootstrap / "alias.html")
    else:
        os.mkfifo(bootstrap / "blocked.pipe")

    proc = subprocess.run(
        [
            "bash",
            str(PREPARE_CACHE),
            "--root",
            str(work / "publisher"),
            "--bootstrap",
            str(bootstrap),
        ],
        env=_prepare_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert output.strip() == "Lab cache public tree validation failed"
    assert excluded not in output.casefold()
    assert (live / "old-only.html").read_text(encoding="utf-8") == "existing last-good"
    assert not (live / "index.html").exists()
    assert not (work / "publisher" / ".layout-v2-scanned-ready").exists()
    assert not list((work / "publisher").glob(".layout-v2-init.*"))


def _seed_identity_variant(bootstrap: Path, variant: str) -> None:
    if variant == "stock-quartz":
        _write_stock_quartz_tree(bootstrap)
    elif variant == "stock-quartz-plus-dummy-plan":
        # The obvious bypass: bolt one plan page onto the stock tree.
        _write_stock_quartz_tree(bootstrap)
        (bootstrap / "plans").mkdir(exist_ok=True)
        (bootstrap / "plans" / "2026-07-25.html").write_text("dummy", encoding="utf-8")
    elif variant == "non-date-plan-filename":
        # `????-??-??.html` also matches this; strict digits must not.
        _write_lab_site_tree(bootstrap)
        for stale in (bootstrap / "plans").glob("[0-9]*.html"):
            stale.unlink()
        (bootstrap / "plans" / "plan-x-y.html").write_text("not a dated page", encoding="utf-8")
    elif variant == "missing-one-identity-route":
        _write_lab_site_tree(bootstrap)
        (bootstrap / "greenhouse" / "index.html").unlink()
    elif variant == "symlinked-identity-route":
        _write_lab_site_tree(bootstrap)
        target = bootstrap / "start" / "index.html"
        target.unlink()
        target.symlink_to(bootstrap / "index.html")
    else:  # pragma: no cover - guards against a typo in the parametrisation
        raise AssertionError(f"unknown variant {variant}")


@pytest.mark.parametrize(
    "variant",
    [
        "stock-quartz",
        "stock-quartz-plus-dummy-plan",
        "non-date-plan-filename",
        "missing-one-identity-route",
        "symlinked-identity-route",
    ],
)
def test_cache_initializer_refuses_a_bootstrap_that_is_not_the_lab_site(tmp_path: Path, variant: str):
    """Regression for 2026-07-26: an emptied cache PVC seeded Quartz's own docs.

    The content-policy scanner passes an upstream Quartz build — nothing in it
    is prohibited, it is simply not this site — so lab.verdify.ai served
    "Welcome to Quartz 4". Identity is checked separately, on the exact copied
    candidate, and a foreign tree fails the rollout rather than reaching the
    public site.
    """
    work = tmp_path / "work"
    work.mkdir(mode=0o770)  # Models the fsGroup-writable PVC root.
    bootstrap = tmp_path / "bootstrap"
    _seed_identity_variant(bootstrap, variant)

    proc = subprocess.run(
        [
            "bash",
            str(PREPARE_CACHE),
            "--root",
            str(work / "publisher"),
            "--bootstrap",
            str(bootstrap),
        ],
        env=_prepare_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode != 0
    assert not (work / "publisher" / "public" / "index.html").exists()
    assert not (work / "publisher" / ".layout-v2-scanned-ready").exists()
    assert not list((work / "publisher").glob(".layout-v2-init.*"))
    if variant == "symlinked-identity-route":
        # The policy scanner owns symlink hazards and rejects first.
        assert proc.stderr.strip() == "Lab cache public tree validation failed"
    else:
        assert proc.stderr.strip() == "Lab cache candidate is not a Verdify Lab build; refusing to install it"


def test_cache_initializer_refuses_to_promote_a_foreign_legacy_tree(tmp_path: Path):
    """The legacy path needs the same identity gate as the bootstrap path.

    On 2026-07-26 the legacy directory was itself the stock Quartz tree, so a
    v1 -> v2 promotion of it would have republished exactly what the incident
    put on the public site.
    """
    work = tmp_path / "work"
    work.mkdir(mode=0o770)
    legacy = work / "public"
    _write_stock_quartz_tree(legacy)

    proc = subprocess.run(
        [
            "bash",
            str(PREPARE_CACHE),
            "--root",
            str(work / "publisher"),
            "--legacy",
            str(legacy),
        ],
        env=_prepare_environment(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode != 0
    assert proc.stderr.strip() == "Lab cache candidate is not a Verdify Lab build; refusing to install it"
    assert not (work / "publisher" / "public" / "index.html").exists()
    # The legacy tree itself is left untouched for the operator to inspect.
    assert (legacy / "index.html").is_file()


def test_cache_initializer_scans_copied_candidate_before_install(tmp_path: Path):
    work = tmp_path / "work"
    live = work / "publisher" / "public"
    bootstrap = tmp_path / "bootstrap"
    live.mkdir(parents=True)
    (live / "old-only.html").write_text("existing last-good", encoding="utf-8")
    _write_lab_site_tree(bootstrap)
    guard_count = tmp_path / "guard-count"
    guard = tmp_path / "guard.sh"
    guard.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
count=0
if [[ -f "$FIXTURE_GUARD_COUNT" ]]; then count="$(<"$FIXTURE_GUARD_COUNT")"; fi
count=$((count + 1))
printf '%s\n' "$count" > "$FIXTURE_GUARD_COUNT"
if [[ "$count" -eq 3 ]]; then exit 7; fi
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            "bash",
            str(PREPARE_CACHE),
            "--root",
            str(work / "publisher"),
            "--bootstrap",
            str(bootstrap),
        ],
        env=_prepare_environment(
            {
                "VERDIFY_PUBLIC_OUTPUT_GUARD": str(guard),
                "VERDIFY_PUBLIC_OUTPUT_PYTHON": "bash",
                "FIXTURE_GUARD_COUNT": str(guard_count),
            }
        ),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode != 0
    assert proc.stderr.strip() == "Lab cache public tree validation failed"
    assert guard_count.read_text(encoding="utf-8").strip() == "3"
    assert (live / "old-only.html").read_text(encoding="utf-8") == "existing last-good"
    assert not (live / "index.html").exists()
    assert not (work / "publisher" / ".layout-v2-scanned-ready").exists()
    assert not list((work / "publisher").glob(".layout-v2-init.*"))


@pytest.mark.parametrize("site_first", [False, True])
def test_cache_initializer_is_independent_of_pod_start_order(tmp_path: Path, site_first: bool):
    work = tmp_path / "work"
    work.mkdir(mode=0o770)
    bootstrap = tmp_path / "bootstrap"
    _write_lab_site_tree(bootstrap)
    common = [
        "bash",
        str(PREPARE_CACHE),
        "--root",
        str(work / "publisher"),
        "--legacy",
        str(work / "public"),
    ]
    publisher_init = common
    site_init = [*common, "--bootstrap", str(bootstrap)]

    first, second = (site_init, publisher_init) if site_first else (publisher_init, site_init)
    subprocess.run(first, env=_prepare_environment(), check=True, capture_output=True, text=True)
    subprocess.run(second, env=_prepare_environment(), check=True, capture_output=True, text=True)

    assert (work / "publisher" / "public" / "index.html").read_text(encoding="utf-8") == "baked fallback"
    assert (work / "publisher" / ".layout-v2-scanned-ready").is_file()


def test_site_initializer_waits_for_active_publisher_lock(harness, tmp_path: Path):
    subprocess.run(
        ["bash", str(PREPARE_CACHE), "--root", str(harness.work)],
        env=_prepare_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    bootstrap = tmp_path / "bootstrap"
    _write_lab_site_tree(bootstrap)

    publisher = harness.start(run_id="publisher", block_sync=True)
    initializer = None
    try:
        deadline = time.monotonic() + 10
        while not harness.block_signal.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert harness.block_signal.exists(), "publisher never acquired its outer lock"

        initializer = subprocess.Popen(
            [
                "bash",
                str(PREPARE_CACHE),
                "--root",
                str(harness.work),
                "--bootstrap",
                str(bootstrap),
            ],
            env=_prepare_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.2)
        assert initializer.poll() is None
        assert not (harness.work / "public" / "index.html").exists()
    finally:
        harness.block_release.touch()

    publisher_stdout, publisher_stderr = publisher.communicate(timeout=20)
    assert publisher.returncode == 0, publisher_stdout + publisher_stderr
    assert initializer is not None
    initializer_stdout, initializer_stderr = initializer.communicate(timeout=20)
    assert initializer.returncode == 0, initializer_stdout + initializer_stderr
    assert (harness.work / "public" / "index.html").read_text(encoding="utf-8") == "baked fallback"


def test_deployed_cache_layout_is_unique_restricted_and_preserves_time_budget():
    publisher_docs = _load_unique_yaml(REPO_ROOT / "deploy/k8s/components/lab-site/lab-publisher.yaml")
    site_docs = _load_unique_yaml(REPO_ROOT / "deploy/k8s/components/lab-site/lab-site.yaml")
    cronjob = _resource(publisher_docs, "CronJob", "verdify-lab-publisher")
    deployment = _resource(site_docs, "Deployment", "verdify-lab")
    nginx_config = _resource(site_docs, "ConfigMap", "verdify-lab-nginx-config")
    publisher_spec = cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    site_spec = deployment["spec"]["template"]["spec"]

    assert cronjob["spec"]["startingDeadlineSeconds"] == 30
    assert publisher_spec["securityContext"]["fsGroupChangePolicy"] == "OnRootMismatch"
    assert site_spec["securityContext"]["fsGroupChangePolicy"] == "OnRootMismatch"

    # STORAGE PLACEMENT. The cache PVC is ReadWriteOnce and node-attached, so an
    # RWO volume is only co-mountable by pods sharing a node. Both workloads must
    # therefore pin the same node, and the Deployment's surge pod (maxSurge 1,
    # maxUnavailable 0 with replicas 1 starts before the old pod drains) must land
    # there too or the rollout stalls on a failed attach. This was live only as an
    # unrecorded hand patch until 2026-07-27; assert it so a sync cannot drop it.
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["rollingUpdate"] == {"maxUnavailable": 0, "maxSurge": 1}
    for pod_spec in (publisher_spec, site_spec):
        assert pod_spec["nodeSelector"] == {"kubernetes.io/hostname": "vm-k3s-node6"}
        cache_volume = next(volume for volume in pod_spec["volumes"] if volume["name"] == "lab-cache")
        assert cache_volume["persistentVolumeClaim"]["claimName"] == "verdify-lab-site-cache"
    cache_pvc = _resource(publisher_docs, "PersistentVolumeClaim", "verdify-lab-site-cache")
    assert cache_pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert [item["name"] for item in publisher_spec["initContainers"]] == ["prepare-private-work-root"]
    assert [item["name"] for item in site_spec["initContainers"]] == [
        "extract-baked-public",
        "prepare-private-public-root",
    ]

    publisher_init = publisher_spec["initContainers"][0]
    site_prepare = site_spec["initContainers"][1]
    assert publisher_init["command"] == [
        "prepare-lab-cache",
        "--root",
        "/work/publisher",
        "--legacy",
        "/work/public",
    ]
    assert site_prepare["command"] == [
        *publisher_init["command"],
        "--bootstrap",
        "/bootstrap",
    ]
    assert site_prepare["image"] == publisher_init["image"]

    site_container = site_spec["containers"][0]
    served_mount = next(mount for mount in site_container["volumeMounts"] if mount["name"] == "lab-cache")
    assert served_mount == {
        "name": "lab-cache",
        "mountPath": "/lab-cache",
        "readOnly": True,
    }
    assert "subPath" not in served_mount
    nginx_default = nginx_config["data"]["default.conf"]
    assert "root /lab-cache/publisher/public;" in nginx_default
    assert "root /usr/share/nginx/html;" not in nginx_default
    assert "try_files $uri $uri.html $uri/index.html =404;" in nginx_default
    assert any(volume == {"name": "bootstrap", "emptyDir": {}} for volume in site_spec["volumes"])

    publisher_container = publisher_spec["containers"][0]
    publisher_env = {item["name"]: item.get("value") for item in publisher_container["env"]}
    assert cronjob["spec"]["schedule"] == "*/10 * * * *"
    assert cronjob["spec"]["concurrencyPolicy"] == "Forbid"
    assert cronjob["spec"]["startingDeadlineSeconds"] == 30
    assert publisher_env["LAB_WORK_ROOT"] == "/work/publisher"
    step_timeout = int(publisher_env["VERDIFY_PUBLISH_STEP_TIMEOUT"])
    rebuild_timeout = int(publisher_env["VERDIFY_PUBLISH_REBUILD_TIMEOUT"])
    job_deadline = cronjob["spec"]["jobTemplate"]["spec"]["activeDeadlineSeconds"]
    assert step_timeout == 180
    assert rebuild_timeout == 300
    assert job_deadline == 1800
    assert step_timeout < rebuild_timeout < job_deadline
    assert job_deadline >= 2 * (step_timeout + rebuild_timeout)

    for pod_spec in (publisher_spec, site_spec):
        pod_security = pod_spec["securityContext"]
        assert pod_security["runAsNonRoot"] is True
        assert pod_security["runAsUser"] == 1000
        assert pod_security["seccompProfile"]["type"] == "RuntimeDefault"
        for container in [*pod_spec["initContainers"], *pod_spec["containers"]]:
            security = container["securityContext"]
            assert security["allowPrivilegeEscalation"] is False
            assert security.get("runAsNonRoot", pod_security["runAsNonRoot"]) is True
            assert security.get("runAsUser", pod_security["runAsUser"]) != 0
            capabilities = security["capabilities"]
            assert capabilities.get("add", []) == []
            assert "ALL" in capabilities["drop"]


def test_publisher_image_and_ci_own_the_canonical_cache_scanner_and_shared_package():
    dockerfile = (REPO_ROOT / "scripts" / "Dockerfile.lab-publisher").read_text(encoding="utf-8")
    initializer = PREPARE_CACHE.read_text(encoding="utf-8")
    ci_gate = (REPO_ROOT / "scripts" / "ci-local.sh").read_text(encoding="utf-8")

    assert "install -m 0755 /app/scripts/check-public-output.py /usr/local/bin/check-public-output" in dockerfile
    assert "VERDIFY_PUBLIC_OUTPUT_GUARD=/usr/local/bin/check-public-output" in dockerfile
    assert "prepare-lab-cache-lock.py /usr/local/bin/prepare-lab-cache-lock" in dockerfile
    assert "VERDIFY_PUBLIC_OUTPUT_GUARD:-/usr/local/bin/check-public-output" in initializer
    assert "VERDIFY_PUBLIC_OUTPUT_GUARD_TIMEOUT:-300" in initializer
    assert "PUBLIC_OUTPUT_GUARD_TIMEOUT > 600" in initializer
    assert initializer.count('validate_public_tree "$BOOTSTRAP"') == 1
    assert initializer.count('validate_public_tree "$LEGACY"') == 1
    assert 'validate_public_tree "$candidate"' in initializer
    assert 'exec 9>"$LOCK_FILE"' not in initializer
    assert 'mkdir -p -- "$LOCK_DIR"' not in initializer
    assert "prepare-lab-cache-lock" in initializer
    assert "prepare-lab-cache-lock" in (REPO_ROOT / "scripts" / "lab-publish-k3s.sh").read_text(encoding="utf-8")

    ruff_lines = [line for line in ci_gate.splitlines() if line.startswith("$RUFF ")]
    assert len(ruff_lines) == 2
    assert all("verdify_public/" in line for line in ruff_lines)
    for test_file in (
        "test_api_public_output_policy.py",
        "test_generate_daily_plan.py",
        "test_lab_publish_k3s_guard.py",
        "test_publish_site_content_guard.py",
        "test_public_output_generators.py",
        "test_public_output_guard.py",
        "test_public_output_policy.py",
        "test_public_output_remediation.py",
        "test_public_zone_renderer.py",
    ):
        assert f"tests/{test_file}" in ci_gate
