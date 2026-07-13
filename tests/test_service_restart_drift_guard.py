"""Tests for the service-restart drift guard (CLAUDE.md rule 7, #391).

The retired ci.yml job matched ``/(restart|bounce|service|systemctl)/i`` — the
bare word "service" appears in nearly every PR body, so any schema-touching
change false-passed and the verdify-mcp/verdify-ingestor bounce was never
actually documented (the 2026-04-21 MCP-staleness incident class). These tests
pin the structural contract of ``scripts/check-service-restart-drift.sh`` so a
future edit can't silently re-open that hole.

No database, no network, no device — diff-mode cases run against throwaway git
repos under tmp_path.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD = REPO_ROOT / "scripts" / "check-service-restart-drift.sh"

GIT_ENV_OVERRIDES = {
    "GIT_AUTHOR_NAME": "guard-test",
    "GIT_AUTHOR_EMAIL": "guard-test@example.invalid",
    "GIT_COMMITTER_NAME": "guard-test",
    "GIT_COMMITTER_EMAIL": "guard-test@example.invalid",
}


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "PR_BODY"}
    env.update(GIT_ENV_OVERRIDES)
    env.update(extra or {})
    return env


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=_env(),
        check=True,
        capture_output=True,
    )


def _make_repo(tmp_path: Path, touched: str, message: str) -> tuple[Path, str]:
    """Base commit + one commit touching ``touched`` with ``message``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, env=_env(), check=True, capture_output=True, text=True
    ).stdout.strip()
    target = repo / touched
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("changed\n")
    _git(repo, "add", touched)
    _git(repo, "commit", "-q", "-m", message)
    return repo, base


def _run_guard(repo: Path, base: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GUARD), base, "HEAD"],
        cwd=repo,
        env=_env(extra_env),
        capture_output=True,
        text=True,
    )


# ── The false-pass that motivated #391 ───────────────────────────────────────


def test_incidental_word_service_fails(tmp_path):
    repo, base = _make_repo(tmp_path, "mcp/server.py", "improved service quality")
    result = _run_guard(repo, base)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "CLAUDE.md rule 7" in result.stderr


def test_bare_restart_without_named_service_fails(tmp_path):
    repo, base = _make_repo(tmp_path, "verdify_schemas/events.py", "restart stuff later maybe")
    result = _run_guard(repo, base)
    assert result.returncode == 1, result.stdout + result.stderr


def test_restart_none_without_reason_fails(tmp_path):
    repo, base = _make_repo(tmp_path, "mcp/server.py", "Restart: none")
    result = _run_guard(repo, base)
    assert result.returncode == 1, result.stdout + result.stderr


# ── Documentation forms that must PASS ───────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "schema: add soil sensor\n\nPost-merge restart: verdify-mcp, verdify-ingestor",
        "bounce verdify-mcp after merge",
        "entity map: rename fan\n\nRestart: none — comment-only change, no consumer bounce needed",
    ],
)
def test_documented_restart_passes(tmp_path, message):
    repo, base = _make_repo(tmp_path, "ingestor/entity_map.py", message)
    result = _run_guard(repo, base)
    assert result.returncode == 0, result.stdout + result.stderr


def test_pr_body_env_supplies_documentation(tmp_path):
    repo, base = _make_repo(tmp_path, "mcp/server.py", "tighten payload validation")
    result = _run_guard(repo, base, {"PR_BODY": "Post-merge restart: verdify-mcp"})
    assert result.returncode == 0, result.stdout + result.stderr


# ── Guard scope: only schema/entity_map/mcp paths trigger it ─────────────────


def test_unguarded_paths_pass_untouched(tmp_path):
    repo, base = _make_repo(tmp_path, "api/routes.py", "improved service quality")
    result = _run_guard(repo, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "not required" in result.stdout


# ── --check-text harness mode ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("this service is great", 1),
        ("Post-merge restart: verdify-mcp, verdify-ingestor", 0),
        ("Restart: none — no consumer bounce needed", 0),
        ("systemctl restart something", 1),
    ],
)
def test_check_text_mode(tmp_path, text, expected):
    fixture = tmp_path / "body.txt"
    fixture.write_text(text + "\n")
    result = subprocess.run(
        ["bash", str(GUARD), "--check-text", str(fixture)],
        env=_env(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == expected, result.stdout + result.stderr
