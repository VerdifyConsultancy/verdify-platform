"""Keep repo-owned GitHub consumers on Agent Fleet's uniform auth boundary.

The rotating credential, expiry validation, locking, ``gh`` wrapper, and Git
askpass implementation are owned and behavior-tested in jvallery/agents#3189.
Verdify must not grow a second token resolver. These tests therefore enforce
the downstream half of the contract: authenticated consumers delegate to the
runtime, while deliberate public reads carry no credential fallback.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_BUILDER = REPO_ROOT / "deploy" / "k8s" / "components" / "firmware-builder" / "firmware-builder.yaml"
QUARTZ_HANDLERS = REPO_ROOT / "site" / "quartz" / "cli" / "handlers.js"
QUARTZ_GIT = REPO_ROOT / "site" / "quartz" / "cli" / "git.js"

AUTH_SURFACE_EXCLUDED_PARTS = {
    ".agent-workflow",
    ".git",
    ".verdify",  # Historical evidence, not executable consumers.
    ".venv",
    "docs",
    "node_modules",
    "tests",  # Fixtures and this test intentionally contain forbidden strings.
}
# Adding a file-aware client is allowed only with operation-time behavioral
# coverage. Until one exists, even reading the standard file handle here would
# create an unreviewed credential path and must fail this closed-set guard.
FORBIDDEN_REPO_AUTH = {
    "token value or local token-file reader": re.compile(
        r"\b(?:GH_TOKEN|GITHUB_TOKEN|GIT_TOKEN|GH_ENTERPRISE_TOKEN|"
        r"GITHUB_ENTERPRISE_TOKEN|GITHUB_TOKEN_FILE|AGENT_GITHUB_TOKEN_FILE)\b",
        re.IGNORECASE,
    ),
    "tokenized GitHub URL or token literal": re.compile(
        r"x-access-token|github_pat_|\bgh[pousr]_[A-Za-z0-9_]+",
        re.IGNORECASE,
    ),
    "repo-local GitHub App identity": re.compile(
        r"\b(?:(?:github|gh)[_-]?(?:app|installation)[_-]?id|"
        r"(?:github|gh)[_-]?(?:app[_-]?)?private[_-]?key)\b",
        re.IGNORECASE,
    ),
    "repo-local central contract declaration": re.compile(
        r"""["']?(?:github_auth|permissions_profile)["']?\s*:""",
        re.IGNORECASE,
    ),
    "persistent or extractive gh auth": re.compile(r"\bgh\s+auth\s+(?:login|logout|refresh|setup-git|switch|token)\b"),
    "direct system gh bypass": re.compile(r"(?:^|[\s'\"])/usr/bin/gh\b"),
    "persistent credential file": re.compile(r"hosts\.yml|\.git-credentials|\.netrc"),
    "persistent git credential helper": re.compile(r"\bgit\s+config[^\n]*credential\.helper"),
    "nonempty inline credential helper": re.compile(
        r"\bcredential\.helper=(?![ \t]*(?:\\|$))[^\s'\"\\]+",
        re.MULTILINE,
    ),
    "git credential extraction": re.compile(r"\bgit\s+credential\s+(?:fill|approve)\b"),
    "repo-owned Git askpass": re.compile(
        r"\bGIT_ASKPASS(?:[\"']?\])?[\"']?[ \t]*(?:=|:)[ \t]*"
        r"(?![\"']?/bin/false(?:[\"']|\b))\S+|"
        r"\bname:[ \t]*GIT_ASKPASS[ \t]*\n[ \t]*value:[ \t]*"
        r"(?![\"']?/bin/false(?:[\"']|\b))\S+",
        re.IGNORECASE,
    ),
    "repo-owned Git core askpass": re.compile(
        r"\bcore\.askPass=(?!/bin/false(?:[ \t\\]|$))[^\s'\"\\]+|"
        r"\bcore\.askPass=[ \t]*([\"'])(?!/bin/false\1)(?:(?!\1)[^\r\n])+\1",
        re.IGNORECASE | re.MULTILINE,
    ),
    "nonempty GitHub HTTP auth header": re.compile(
        r"\bhttp\.extraHeader=(?![ \t\\]|$)[^\s'\"\\]+|"
        r"\bhttp\.extraHeader=[ \t]*([\"'])(?:(?!\1)[^\r\n])+\1",
        re.IGNORECASE | re.MULTILINE,
    ),
    "Git auth config mutation": re.compile(
        r"\bgit(?:[ \t]+-C[ \t]+\S+)?[ \t]+config[ \t]+"
        r"(?:(?:--global|--local|--system|--worktree)[ \t]+)?"
        r"(?:core\.askPass|credential\.[^\s]+|http\.[^\s]+)|"
        r"[\"']git[\"']\s*,\s*[\[(]\s*[\"']config[\"']\s*,\s*"
        r"(?:[\"']--(?:global|local|system|worktree)[\"']\s*,\s*)?"
        r"[\"'](?:core\.askPass|credential\.[^\"']+|http\.[^\"']+)[\"']",
        re.IGNORECASE,
    ),
    "repo-owned Git SSH command": re.compile(r"\bGIT_SSH_COMMAND\b", re.IGNORECASE),
    "SSH GitHub remote": re.compile(r"(?:git@github\.com|ssh://git@github\.com)"),
    "credential-bearing HTTPS GitHub URL": re.compile(r"https://[^/\s]+@github\.com"),
    "direct GitHub API client": re.compile(r"api\.github\.com"),
    "hard-coded GitHub token Secret": re.compile(r"\b[a-z0-9-]*github-token\b", re.IGNORECASE),
    "retired cross-repository target": re.compile(
        r"VerdifyConsultancy/verdify-site-legacy",
        re.IGNORECASE,
    ),
}

ACTIONS_JOB_TOKEN_BINDING = re.compile(
    r"(?m)^[ \t]*(?:GH_TOKEN|GITHUB_TOKEN|token):[ \t]*"
    r"\$\{\{[ \t]*(?:github\.token|secrets\.GITHUB_TOKEN)[ \t]*\}\}[ \t]*$"
)

NETWORK_GIT_PATTERNS = (
    re.compile(
        r"\bgit(?:[ \t\\\r\n]+-c[ \t]+[^\s\\]+[ \t\\\r\n]*)*"
        r"[ \t\\\r\n]+(?:clone|fetch|pull|push|ls-remote)\b"
    ),
    re.compile(
        r"(?<![A-Za-z0-9_])git(?![A-Za-z0-9_])[^\r\n]{0,240}?"
        r"\b(?:clone|fetch|pull|push|ls-remote)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:spawn|spawnSync|execFile|execFileSync)\(\s*[\"']git[\"']\s*,\s*"
        r"[\[(]\s*[\"'](?:clone|fetch|pull|push|ls-remote)[\"']"
    ),
    re.compile(
        r"subprocess\.(?:run|Popen|check_call|check_output)\(\s*"
        r"[\[(]\s*[\"']git[\"']\s*,\s*[\"'](?:clone|fetch|pull|push|ls-remote)[\"']"
    ),
    re.compile(
        r"[\"']git[\"']\s*,[\s\S]{0,160}?[\"']"
        r"(?:clone|fetch|pull|push|ls-remote)[\"']"
    ),
    re.compile(r"runGit\(\s*\[\s*[\"'](?:clone|fetch|pull|push|ls-remote)[\"']"),
    re.compile(r"\bgit(?:Pull|Push)\s*\("),
)

GH_SUBCOMMAND = (
    r"(?:alias|api|attestation|auth|browse|cache|codespace|completion|config|"
    r"extension|gist|gpg-key|issue|label|pr|release|repo|ruleset|run|search|"
    r"secret|ssh-key|status|variable|workflow)"
)
GH_OPERATION_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_])gh[ \t]+{GH_SUBCOMMAND}\b|"
    rf"[\"']gh[\"']\s*,\s*(?:[\[(]\s*)?[\"']{GH_SUBCOMMAND}[\"']",
    re.IGNORECASE,
)

GITHUB_HTTP_HOST = re.compile(
    r"(?:github\.com|api\.github\.com|[A-Za-z0-9.-]*githubusercontent\.com)",
    re.IGNORECASE,
)
HTTP_CLIENT_PATTERN = re.compile(
    r"\b(?:fetch|urlopen|axios\.(?:get|post|put|patch|delete)|"
    r"requests\.(?:get|post|put|patch|delete)|httpx\.(?:get|post|put|patch|delete)|"
    r"https?\.(?:get|request)|urllib\.request|curl|wget)\b",
    re.IGNORECASE,
)
DIRECT_GITHUB_HTTP_PATTERN = re.compile(
    r"\b(?:fetch|urlopen|axios\.(?:get|post|put|patch|delete)|"
    r"requests\.(?:get|post|put|patch|delete)|httpx\.(?:get|post|put|patch|delete)|"
    r"https?\.(?:get|request))\s*\(\s*(?:new[ \t]+URL\s*\(\s*)?"
    r"[\"'`]https://(?:github\.com|api\.github\.com|"
    r"[A-Za-z0-9.-]*githubusercontent\.com)/|"
    r"\b(?:curl|wget)\b[^\n]*https://(?:github\.com|api\.github\.com|"
    r"[A-Za-z0-9.-]*githubusercontent\.com)/",
    re.IGNORECASE,
)
GITHUB_URL_ASSIGNMENT = re.compile(
    r"\b(?:const|let|var)?[ \t]*(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)[ \t]*="
    r"[ \t]*(?:new[ \t]+URL\s*\(\s*)?[\"'`]https://"
    r"(?:github\.com|api\.github\.com|[A-Za-z0-9.-]*githubusercontent\.com)/",
    re.IGNORECASE,
)

HISTORICAL_DOC_PREFIXES = {
    ("docs", "design"),
    ("docs", "proposals"),
    ("docs", "reviews"),
    ("docs", "roadmap"),
    ("docs", "templates"),
}
HISTORICAL_DOCS = {Path("docs/SYSTEM-ARCHITECTURE.md")}
MANAGED_FLEET_BRIEFING = re.compile(
    r"<!-- BEGIN agent-fleet operating-environment briefing.*?"
    r"<!-- END agent-fleet operating-environment briefing \(managed #2302\) -->",
    re.DOTALL,
)
ACTIVE_DOC_FORBIDDEN = {
    "token assignment/export": re.compile(
        r"(?m)^[ \t]*(?:[-*][ \t]+|[$>][ \t]+)?(?:export[ \t]+)?"
        r"(?:GH_TOKEN|GITHUB_TOKEN|GIT_TOKEN|GH_ENTERPRISE_TOKEN|GITHUB_ENTERPRISE_TOKEN)"
        r"[ \t]*(?:=|:)",
        re.IGNORECASE,
    ),
    "interactive gh credential setup": re.compile(
        r"(?m)^[ \t]*(?:[-*][ \t]+|[$>][ \t]+)?`?gh[ \t]+auth[ \t]+"
        r"(?:login|logout|refresh|setup-git|switch|token)\b",
        re.IGNORECASE,
    ),
    "PAT/token literal": re.compile(r"github_pat_|\bgh[pousr]_[A-Za-z0-9_]+|scoped PAT", re.IGNORECASE),
    "SSH GitHub command": re.compile(
        r"\bgit[ \t]+(?:clone|fetch|pull|push)[^\n]*(?:git@github\.com|ssh://git@github\.com)",
        re.IGNORECASE,
    ),
    "direct authenticated GitHub API command": re.compile(
        r"\b(?:curl|wget)\b[^\n]*api\.github\.com",
        re.IGNORECASE,
    ),
    "repo-local central contract declaration": re.compile(
        r"(?m)^[ \t]*(?:github_auth|permissions_profile)[ \t]*:",
        re.IGNORECASE,
    ),
    "hard-coded GitHub token Secret": re.compile(r"\b[a-z0-9-]*github-token\b", re.IGNORECASE),
}


def _contains_network_git(text: str) -> bool:
    return any(pattern.search(text) for pattern in NETWORK_GIT_PATTERNS)


def _contains_gh_operation(text: str) -> bool:
    if GH_OPERATION_PATTERN.search(text):
        return True
    return bool(
        re.search(r"\b(?:spawn|spawnSync|exec|execSync|execFile|execFileSync|subprocess\.)\b", text)
        and re.search(r"[\"']gh[\"']", text, re.IGNORECASE)
        and re.search(rf"[\"']{GH_SUBCOMMAND}[\"']", text, re.IGNORECASE)
    )


def _contains_direct_github_http(text: str) -> bool:
    if DIRECT_GITHUB_HTTP_PATTERN.search(text):
        return True
    for assignment in GITHUB_URL_ASSIGNMENT.finditer(text):
        following = text[assignment.end() : assignment.end() + 4_000]
        name = re.escape(assignment.group("name"))
        if re.search(
            rf"\b(?:fetch|urlopen|axios\.(?:get|post)|requests\.(?:get|post)|"
            rf"httpx\.(?:get|post)|https?\.(?:get|request))\s*\(\s*{name}\b",
            following,
            re.IGNORECASE,
        ):
            return True
    return bool("DOWNLOAD_HOSTS" in text and GITHUB_HTTP_HOST.search(text) and HTTP_CLIENT_PATTERN.search(text))


def _auth_surface_files() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    files: list[Path] = []
    for raw in tracked:
        if not raw:
            continue
        path = REPO_ROOT / raw.decode("utf-8")
        if not path.is_file():
            continue
        if AUTH_SURFACE_EXCLUDED_PARTS.intersection(path.relative_to(REPO_ROOT).parts):
            continue
        # Operational Markdown has a separate scanner below so explanatory
        # mentions do not masquerade as executable consumers.
        if path.suffix == ".md":
            continue
        if ".sops." in path.name:
            continue
        try:
            content = path.read_bytes()
            if b"\0" not in content:
                content.decode("utf-8")
                files.append(path)
        except (OSError, UnicodeDecodeError):
            continue
    return sorted(files)


def _active_operational_docs() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    docs: list[Path] = []
    for raw in tracked:
        if not raw:
            continue
        relative = Path(raw.decode("utf-8"))
        if relative.suffix != ".md" or relative in HISTORICAL_DOCS:
            continue
        if any(relative.parts[: len(prefix)] == prefix for prefix in HISTORICAL_DOC_PREFIXES):
            continue
        if len(relative.parts) == 1 or relative.parts[0] in {"deploy", "docs"}:
            docs.append(REPO_ROOT / relative)
    return sorted(docs)


def _scan_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(REPO_ROOT)
    if relative.parts[:2] == (".github", "workflows"):
        # The built-in per-job token is a separate contract. Only an exact
        # workflow-local binding is exempt; PAT/App/login paths remain scanned.
        text = ACTIONS_JOB_TOKEN_BINDING.sub("", text)
    return text


def test_repo_has_no_local_or_persistent_github_auth_path():
    for sample in (
        "git config core.askPass ./credential-helper",
        "git config --global http.extraHeader 'Authorization: credential-sentinel'",
        'http.extraHeader="Authorization: credential-sentinel"',
        '{"GIT_ASKPASS": credential_helper}',
    ):
        assert any(pattern.search(sample) for pattern in FORBIDDEN_REPO_AUTH.values())

    violations: list[str] = []
    for path in _auth_surface_files():
        text = _scan_text(path)
        for description, pattern in FORBIDDEN_REPO_AUTH.items():
            if pattern.search(text):
                violations.append(f"{path.relative_to(REPO_ROOT)}: {description}")

    assert violations == [], (
        "GitHub authentication belongs to the Agent Fleet runtime; repo-owned "
        "token/App/login/fallback paths are forbidden:\n" + "\n".join(violations)
    )


def test_active_operational_docs_do_not_prescribe_snowflake_auth():
    cleanup_docs = {
        Path("deploy/k8s/SECRETS.md"),
        Path("docs/handoff/k3s-agent-handoff.md"),
    }
    violations: list[str] = []
    for path in _active_operational_docs():
        relative = path.relative_to(REPO_ROOT)
        text = MANAGED_FLEET_BRIEFING.sub("", path.read_text(encoding="utf-8"))
        text = ACTIONS_JOB_TOKEN_BINDING.sub("", text)
        if relative in cleanup_docs:
            assert re.search(r"Secret `verdify-github-token`, key\s+`GITHUB_TOKEN`", text)
            text = text.replace("verdify-github-token", "pending-legacy-secret-cleanup")
        for description, pattern in ACTIVE_DOC_FORBIDDEN.items():
            if pattern.search(text):
                violations.append(f"{relative}: {description}")

    assert violations == [], (
        "Active operational documentation must delegate GitHub authentication "
        "to Agent Fleet and may not prescribe a local fallback:\n" + "\n".join(violations)
    )


def test_github_consumer_inventory_is_closed():
    network_git: set[str] = set()
    gh_operations: set[str] = set()
    direct_github_http: set[str] = set()

    for sample in (
        'subprocess.run(("git", "push", "origin", "main"))',
        'spawn("git", ["push", "origin", "main"])',
        "git -C repo push origin main",
    ):
        assert _contains_network_git(sample)
    for sample in (
        "gh status",
        "if gh pr list; then :; fi",
        "result=$(gh api repos/example/repo)",
        'GH_HOST=github.com gh status; execSync("gh pr list")',
    ):
        assert _contains_gh_operation(sample)
    for sample in (
        'fetch("https://github.com/example/repo/releases/download/v1/asset")',
        'const url = "https://github.com/example/repo"; fetch(url)',
        'fetch(new URL("https://api.github.com/repos/example/repo"))',
        'release_url = "https://github.com/example/repo"\nurlopen(release_url)',
    ):
        assert _contains_direct_github_http(sample)

    for path in _auth_surface_files():
        relative = str(path.relative_to(REPO_ROOT))
        text = _scan_text(path)
        executable_text = re.sub(r"(?m)^\s*(?://|#).*$", "", text)
        if _contains_network_git(executable_text):
            network_git.add(relative)
        if _contains_gh_operation(executable_text):
            gh_operations.add(relative)
        if _contains_direct_github_http(executable_text):
            direct_github_http.add(relative)

    assert network_git == {
        "deploy/k8s/components/firmware-builder/firmware-builder.yaml",
        "site/quartz/cli/git.js",
        "site/quartz/cli/handlers.js",
    }
    assert gh_operations == set()
    assert direct_github_http == {"site-astro/scripts/fetch-stage-snapshot.mjs"}


def test_firmware_builder_public_read_has_no_credential_fallback():
    manifest = FIRMWARE_BUILDER.read_text(encoding="utf-8")
    documents = list(yaml.safe_load_all(manifest))
    cronjob = next(document for document in documents if document.get("kind") == "CronJob")
    clone_builder = next(
        container
        for container in cronjob["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
        if container["name"] == "builder"
    )
    script = "\n".join(clone_builder["args"])

    assert "Source acquisition is intentionally credentialless" in manifest
    assert 'PUBLIC_GIT_HOME="$(mktemp -d /tmp/verdify-public-git-home.XXXXXX)"' in script
    assert "env -i" in script
    assert "GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null" in script
    assert "GIT_ASKPASS=/bin/false SSH_ASKPASS=/bin/false GIT_TERMINAL_PROMPT=0" in script
    assert "git -c credential.helper= -c core.askPass=/bin/false -c http.extraHeader=" in script
    assert re.findall(r"\bclone\b", script) == ["clone"]
    assert "--filter" not in script
    assert script.count("https://github.com/VerdifyConsultancy/verdify-platform.git") == 1
    assert "git -C /work -c credential.helper= -c core.askPass=/bin/false -c http.extraHeader=" in script
    assert 'checkout -q "${FW_REVISION}"' in script

    for variable in clone_builder.get("env", []):
        assert not re.search(r"(?:github|git|token)", variable["name"], re.IGNORECASE)
        secret_ref = variable.get("valueFrom", {}).get("secretKeyRef", {})
        assert not re.search(
            r"(?:github|token)",
            f"{secret_ref.get('name', '')} {secret_ref.get('key', '')}",
            re.IGNORECASE,
        )


def test_firmware_public_read_rejects_hostile_ambient_auth(tmp_path: Path):
    marker = tmp_path / "ambient-askpass-used"
    hostile_askpass = tmp_path / "hostile-askpass"
    hostile_askpass.write_text(
        "#!/bin/sh\ntouch \"$HOSTILE_ASKPASS_MARKER\"\nprintf '%s\\n' ambient-token-sentinel\n",
        encoding="utf-8",
    )
    hostile_askpass.chmod(0o700)
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()

    class UnauthorizedGitHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="auth-contract-test"')
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), UnauthorizedGitHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        hostile_environment = os.environ.copy()
        hostile_environment.update(
            {
                "GIT_ASKPASS": str(hostile_askpass),
                "SSH_ASKPASS": str(hostile_askpass),
                "HOSTILE_ASKPASS_MARKER": str(marker),
            }
        )
        result = subprocess.run(
            [
                "env",
                "-i",
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                f"HOME={empty_home}",
                "GIT_CONFIG_NOSYSTEM=1",
                "GIT_CONFIG_GLOBAL=/dev/null",
                "GIT_ASKPASS=/bin/false",
                "SSH_ASKPASS=/bin/false",
                "GIT_TERMINAL_PROMPT=0",
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "core.askPass=/bin/false",
                "-c",
                "http.extraHeader=",
                "ls-remote",
                f"http://127.0.0.1:{server.server_port}/private.git",
            ],
            check=False,
            capture_output=True,
            env=hostile_environment,
            text=True,
            timeout=10,
        )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    assert result.returncode != 0
    assert not marker.exists()
    assert "ambient-token-sentinel" not in result.stdout
    assert "ambient-token-sentinel" not in result.stderr


def test_retired_cross_repo_writer_cannot_regrow_a_static_credential_path():
    assert not (REPO_ROOT / "scripts" / "sync-lab-content.sh").exists()

    active_contract_docs = (
        REPO_ROOT / "ACCESS_MATRIX.md",
        REPO_ROOT / "deploy" / "k8s" / "SECRETS.md",
        REPO_ROOT / "docs" / "handoff" / "k3s-agent-handoff.md",
        REPO_ROOT / "docs" / "runbooks" / "lab-content-pipeline.md",
        REPO_ROOT / "docs" / "runbooks" / "laptop-operator.md",
        REPO_ROOT / "planning" / "backlog.yaml",
    )
    cleanup_docs = {
        REPO_ROOT / "deploy" / "k8s" / "SECRETS.md",
        REPO_ROOT / "docs" / "handoff" / "k3s-agent-handoff.md",
    }
    forbidden = re.compile(r"LAB_REPO_TOKEN|verdify-github-token|scoped PAT", re.IGNORECASE)
    violations: list[str] = []
    for path in active_contract_docs:
        text = path.read_text(encoding="utf-8")
        if path in cleanup_docs:
            assert re.search(r"Secret `verdify-github-token`, key\s+`GITHUB_TOKEN`", text)
            assert re.search(r"\bdelet(?:e|ing)\b", text)
            text = text.replace("verdify-github-token", "pending-legacy-secret-cleanup")
        if forbidden.search(text):
            violations.append(str(path.relative_to(REPO_ROOT)))
    assert violations == []


def test_intentional_public_and_external_reads_are_credential_free():
    source = (REPO_ROOT / "site-astro" / "scripts" / "fetch-stage-snapshot.mjs").read_text(encoding="utf-8")

    assert "snapshot asset URL must be credential-free HTTPS" in source
    assert "assetSha256" in source
    assert "attestationSha256" in source
    assert "Authorization" not in source

    citation_source = (REPO_ROOT / "site" / "quartz" / "plugins" / "transformers" / "citations.ts").read_text(
        encoding="utf-8"
    )
    quartz_config = (REPO_ROOT / "site" / "quartz.config.ts").read_text(encoding="utf-8")
    assert "https://raw.githubusercontent.com/citation-stylelanguage/locales/" in citation_source
    assert "Authorization" not in citation_source
    assert 'locale: "en-US"' in quartz_config

    precommit = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hook_repositories = {entry["repo"] for entry in precommit["repos"]}
    assert hook_repositories == {
        "https://github.com/astral-sh/ruff-pre-commit",
        "https://github.com/pre-commit/pre-commit-hooks",
    }
    for repository in hook_repositories:
        parsed = urlsplit(repository)
        assert parsed.scheme == "https"
        assert parsed.username is None
        assert parsed.password is None

    expected_repo = "https://github.com/VerdifyConsultancy/verdify-platform.git"
    for relative in (
        "deploy/k8s/argocd/apps/verdify-prod.yaml",
        "deploy/k8s/argocd/apps/verdify-prod-dark.yaml",
    ):
        application = yaml.safe_load((REPO_ROOT / relative).read_text(encoding="utf-8"))
        assert application["spec"]["source"]["repoURL"] == expected_repo
        assert application["metadata"]["annotations"]["agent-fleet.vallery.net/app-repo"] == expected_repo
        assert application["metadata"]["annotations"]["agent-fleet.vallery.net/source-repo"] == expected_repo


def test_manual_quartz_git_operations_delegate_and_fail_closed(tmp_path: Path):
    source = QUARTZ_HANDLERS.read_text(encoding="utf-8")
    sync_handler = source[source.index("export async function handleSync") :]

    # Each operation spawns ordinary Git, which makes the fleet askpass resolve
    # the current token for that operation. No token is held by the Node process.
    assert "gitPull(ORIGIN_NAME, QUARTZ_SOURCE_BRANCH)" in sync_handler
    assert "gitPush(ORIGIN_NAME, currentBranch)" in sync_handler
    assert "gitPublicPull(UPSTREAM_NAME, QUARTZ_SOURCE_BRANCH)" in source

    # A missing/expired runtime credential makes Git fail. The manual command
    # must propagate that failure rather than reporting success or falling back.
    assert re.search(
        r"catch \(error\).*?await popContentFolder\(contentFolder\).*?throw error",
        sync_handler,
        re.DOTALL,
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        'operation=""\n'
        'for argument in "$@"; do\n'
        '  case "$argument" in config|remote|pull|push) operation="$argument"; break ;; esac\n'
        "done\n"
        'if [ "$operation" = "config" ]; then exit 1; fi\n'
        'if [ "$operation" = "remote" ]; then\n'
        '  printf "%s\\n" "${FAKE_GIT_REMOTE_URL:-https://github.com/VerdifyConsultancy/verdify-platform.git}"\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$GIT_ASKPASS" != "$EXPECTED_GIT_ASKPASS" ]; then exit 7; fi\n'
        'if [ "$SSH_ASKPASS" != "/bin/false" ]; then exit 8; fi\n'
        'if [ "$GIT_TERMINAL_PROMPT" != "0" ]; then exit 9; fi\n'
        'generation="$(cat "$GIT_GENERATION_FILE")"\n'
        'printf "%s:%s:%s\\n" "$operation" "$generation" "$*" >> "$GIT_INVOCATION_LOG"\n'
        'if [ "$operation" = "${FAIL_GIT_OPERATION:-}" ]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o700)
    generation_file = tmp_path / "generation"
    invocation_log = tmp_path / "invocations"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "GIT_GENERATION_FILE": str(generation_file),
            "GIT_INVOCATION_LOG": str(invocation_log),
            "EXPECTED_GIT_ASKPASS": "/fleet/runtime/askpass",
            "GIT_ASKPASS": "/fleet/runtime/askpass",
            "SSH_ASKPASS": "/credential-sentinel-ssh-askpass",
            "GIT_TERMINAL_PROMPT": "1",
        }
    )

    module_url = json.dumps(QUARTZ_GIT.as_uri())
    rotation_script = f"""
      import {{ writeFileSync }} from "node:fs";
      import {{ gitPull, gitPush }} from {module_url};
      writeFileSync(process.env.GIT_GENERATION_FILE, "generation-one");
      gitPull("origin", "v4");
      writeFileSync(process.env.GIT_GENERATION_FILE, "generation-two");
      gitPush("origin", "v4");
    """
    rotated = subprocess.run(
        ["node", "--input-type=module", "--eval", rotation_script],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )
    assert rotated.returncode == 0, rotated.stderr
    invocations = invocation_log.read_text(encoding="utf-8").splitlines()
    expected_remote = "https://github.com/VerdifyConsultancy/verdify-platform.git"
    assert len(invocations) == 2
    assert invocations[0].startswith("pull:generation-one:")
    assert invocations[1].startswith("push:generation-two:")
    for invocation in invocations:
        assert expected_remote in invocation
        assert " origin " not in f" {invocation} "

    environment["FAIL_GIT_OPERATION"] = "push"
    failure_script = f'import {{ gitPush }} from {module_url}; gitPush("origin", "v4");'
    failed = subprocess.run(
        ["node", "--input-type=module", "--eval", failure_script],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )
    assert failed.returncode != 0
    assert "git push origin/v4 failed" in failed.stderr

    before_rejections = invocation_log.read_text(encoding="utf-8")
    for unsafe_remote in (
        "git@github.com:VerdifyConsultancy/verdify-platform.git",
        "https://credential-sentinel@github.com/VerdifyConsultancy/verdify-platform.git",
        "https://example.invalid/VerdifyConsultancy/verdify-platform.git",
    ):
        environment["FAKE_GIT_REMOTE_URL"] = unsafe_remote
        rejected = subprocess.run(
            ["node", "--input-type=module", "--eval", failure_script],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
            timeout=10,
        )
        assert rejected.returncode != 0
        assert unsafe_remote not in rejected.stdout
        assert unsafe_remote not in rejected.stderr
    assert invocation_log.read_text(encoding="utf-8") == before_rejections

    # Exercise the validation against a real repository/config parser while a
    # PATH shim intercepts only the final network operation. Config values are
    # synthetic sentinels and must never appear in the thrown error.
    real_git = shutil.which("git")
    assert real_git is not None
    hostile_repo = tmp_path / "hostile-repo"
    subprocess.run([real_git, "init", "-q", str(hostile_repo)], check=True)
    subprocess.run(
        [
            real_git,
            "-C",
            str(hostile_repo),
            "remote",
            "add",
            "origin",
            "https://github.com/VerdifyConsultancy/verdify-platform.git",
        ],
        check=True,
    )
    network_marker = tmp_path / "network-operation-ran"
    fake_git.write_text(
        "#!/bin/sh\n"
        'operation=""\n'
        'for argument in "$@"; do\n'
        '  case "$argument" in config|remote|pull|push) operation="$argument"; break ;; esac\n'
        "done\n"
        'case "$operation" in\n'
        f'  config|remote) exec {shlex.quote(real_git)} "$@" ;;\n'
        f"  pull|push) touch {shlex.quote(str(network_marker))}; exit 0 ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o700)

    hostile_configs = (
        ("credential.https://github.com.helper", "credential-sentinel-helper"),
        ("http.https://github.com/.extraHeader", "Authorization: credential-sentinel-header"),
        ("http.https://github.com/.cookieFile", str(tmp_path / "credential-sentinel-cookie")),
        ("http.sslVerify", "false"),
        ("http.sslCAInfo", str(tmp_path / "credential-sentinel-ca")),
        ("http.curloptResolve", "github.com:443:127.0.0.1"),
        ("core.askPass", str(tmp_path / "credential-sentinel-askpass")),
    )
    environment.pop("FAKE_GIT_REMOTE_URL", None)
    for key, value in hostile_configs:
        subprocess.run(
            [real_git, "-C", str(hostile_repo), "config", "--local", key, value],
            check=True,
        )
        rejected = subprocess.run(
            ["node", "--input-type=module", "--eval", failure_script],
            cwd=hostile_repo,
            check=False,
            capture_output=True,
            env=environment,
            text=True,
            timeout=10,
        )
        assert rejected.returncode != 0
        assert "credential-sentinel" not in rejected.stdout
        assert "credential-sentinel" not in rejected.stderr
        assert not network_marker.exists()
        subprocess.run(
            [real_git, "-C", str(hostile_repo), "config", "--local", "--unset-all", key],
            check=True,
        )

    subprocess.run(
        [
            real_git,
            "-C",
            str(hostile_repo),
            "remote",
            "set-url",
            "--add",
            "--push",
            "origin",
            "https://github.com/VerdifyConsultancy/verdify-platform.git",
        ],
        check=True,
    )
    subprocess.run(
        [
            real_git,
            "-C",
            str(hostile_repo),
            "remote",
            "set-url",
            "--add",
            "--push",
            "origin",
            "git@github.com:VerdifyConsultancy/verdify-platform.git",
        ],
        check=True,
    )
    multiple_push_urls = subprocess.run(
        ["node", "--input-type=module", "--eval", failure_script],
        cwd=hostile_repo,
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )
    assert multiple_push_urls.returncode != 0
    assert not network_marker.exists()

    subprocess.run(
        [
            real_git,
            "-C",
            str(hostile_repo),
            "remote",
            "set-url",
            "--add",
            "origin",
            "git@github.com:VerdifyConsultancy/verdify-platform.git",
        ],
        check=True,
    )
    multiple_fetch_urls = subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            f'import {{ gitPull }} from {module_url}; gitPull("origin", "v4");',
        ],
        cwd=hostile_repo,
        check=False,
        capture_output=True,
        env=environment,
        text=True,
        timeout=10,
    )
    assert multiple_fetch_urls.returncode != 0
    assert not network_marker.exists()
