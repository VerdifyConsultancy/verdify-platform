from pathlib import Path

from verdify_public.build_provenance import image_git_sha

REPO_ROOT = Path(__file__).resolve().parents[1]
SELECTED_DOCKERFILES = (
    "api/Dockerfile",
    "mcp/Dockerfile",
    "ingestor/Dockerfile",
    "experiment_orchestrator/Dockerfile",
)


def test_runtime_revision_prefers_valid_build_arg_then_uses_baked_receipt(tmp_path):
    receipt = tmp_path / "source-revision"
    receipt.write_text("b" * 40 + "\n")

    assert image_git_sha("a" * 40, receipt) == "a" * 40
    assert image_git_sha("unknown", receipt) == "b" * 40
    assert image_git_sha("", receipt) == "b" * 40

    receipt.write_text("ref: refs/heads/main\n")
    assert image_git_sha("unknown", receipt) == "unknown"
    assert image_git_sha("A" * 40, receipt) == "unknown"
    assert image_git_sha("unknown", tmp_path / "missing") == "unknown"


def test_managed_build_context_exposes_only_detached_head_metadata():
    dockerignore = (REPO_ROOT / ".dockerignore").read_text().splitlines()
    git_rules = [line for line in dockerignore if line in {".git", "!.git", ".git/*", "!.git/HEAD"}]
    assert git_rules == [".git", "!.git", ".git/*", "!.git/HEAD"]

    api_dockerfile = (REPO_ROOT / "api/Dockerfile").read_text()
    assert "COPY .git /tmp/verdify-git-metadata" in api_dockerfile
    assert "grep -Eq '^[0-9a-f]{40}$'" in api_dockerfile
    assert "COPY --from=source-metadata /out/source-revision /etc/verdify/source-revision" in api_dockerfile


def test_dockerfiles_do_not_override_managed_reserved_oci_labels():
    for relative in SELECTED_DOCKERFILES:
        dockerfile = (REPO_ROOT / relative).read_text()
        assert "org.opencontainers.image.revision=$GIT_SHA" not in dockerfile
        assert "org.opencontainers.image.created=$BUILD_TIME" not in dockerfile
        assert "org.opencontainers.image.source=" in dockerfile
