"""ci/validate.Dockerfile contract: the validate CI image with PostgreSQL 16 preinstalled.

jvallery/agents pins this image in WorkflowTemplate agent-fleet-ci/verdify-platform-ci, template ``validate``,
which runs scripts/ci-local.sh. It is the python image that template ran before, plus the PGDG PostgreSQL 16
toolchain. With the toolchain installed, ``postgres_bin()`` in scripts/qualify-component-containment.py no longer
downloads it on every run. Kaniko reads ci/validate.Dockerfile.dockerignore in place of the root .dockerignore,
so the ignore file must admit exactly the Dockerfile's COPY inputs.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import re
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "ci/validate.Dockerfile"
DOCKERIGNORE = REPO_ROOT / "ci/validate.Dockerfile.dockerignore"
# The validate template's container image, read from the live template on 2026-11-02 (python:3.13-bookworm). This
# image builds on it, so the validate environment stays that image plus PostgreSQL 16. Bump the two together.
VALIDATE_TEMPLATE_IMAGE = (
    "mirror.gcr.io/library/python@sha256:353cf2106d143e1d28f5d7c10c5f5c0387085bba22ef0f7f7e52c2c330fb1779"
)
PGDG_KEY = "db/qualification/pgdg-ACCC4CF8.asc"
KEYRING = "/etc/apt/keyrings/pgdg-ACCC4CF8.asc"
PGCRYPTO = "/usr/share/postgresql/16/extension/pgcrypto.control"
KUSTOMIZE_SHA256 = "3669470b454d865c8184d6bce78df05e977c9aea31c30df3c669317d43bcc7a7"


def _text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


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


def _final_run(marker: str) -> str:
    runs = [line for line in _stages(_text())[-1]["lines"] if _keyword(line) == "RUN" and marker in line]
    assert len(runs) == 1, runs
    return runs[0]


def _qualifier():
    spec = importlib.util.spec_from_file_location(
        "qualify_component_containment", REPO_ROOT / "scripts/qualify-component-containment.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _kaniko_excludes(path: str, rules: list[str]) -> bool:
    """moby/patternmatcher v0.6.0 ``Matches``, which Kaniko v1.23.2 applies to each build-context path.

    Rules apply in order. A rule is tried against the path and then against the path's leading directories cut to
    the rule's depth. Only literal and ``*`` rules are modelled; the caller asserts the file uses no others.
    """
    matched = False
    parents = PurePosixPath(path).parent.parts
    for rule in rules:
        exclusion = rule.startswith("!")
        if exclusion != matched:
            continue
        pattern = rule.removeprefix("!")
        regex = "[^/]*".join(re.escape(part) for part in pattern.split("*"))
        depth = len(pattern.split("/"))
        hit = re.fullmatch(regex, path) is not None
        if not hit and parents and depth <= len(parents):
            hit = re.fullmatch(regex, "/".join(parents[:depth])) is not None
        if hit:
            matched = not exclusion
    return matched


def test_every_from_is_the_python_image_the_validate_template_runs():
    stages = _stages(_text())
    assert [(stage["base"], stage["name"]) for stage in stages] == [
        (VALIDATE_TEMPLATE_IMAGE, "source-metadata"),
        (VALIDATE_TEMPLATE_IMAGE, None),
    ]


def test_no_forbidden_instructions():
    seen: list[str] = []
    for stage in _stages(_text()):
        for line in stage["lines"]:
            # ADD, a COPY flag other than --from=<earlier stage> (--chown, --chmod, --link) or a $ or backslash in a
            # COPY line makes the in-cluster Kaniko path rebuild every image.
            assert _keyword(line) != "ADD", line
            if _keyword(line) == "COPY":
                flags = [word for word in line.split()[1:] if word.startswith("--")]
                assert flags == [] or (len(flags) == 1 and flags[0].removeprefix("--from=") in seen), line
                assert "$" not in line and "\\" not in line, line
        if stage["name"]:
            seen.append(stage["name"])
    # The validate step runs `bash -euc` as root in the base's environment, so the final stage only adds files: no
    # ENV, USER, WORKDIR, ENTRYPOINT, CMD or anything else that changes how its script runs.
    assert {_keyword(line) for line in _stages(_text())[-1]["lines"]} <= {"COPY", "RUN", "LABEL"}


def test_pgdg_key_is_the_vendored_copy_checked_against_the_pinned_hash_before_apt_uses_it():
    qualifier = _qualifier()
    assert qualifier.PGDG_KEY_FILE == REPO_ROOT / PGDG_KEY
    assert hashlib.sha256(qualifier.PGDG_KEY_FILE.read_bytes()).hexdigest() == qualifier.PGDG_KEY_SHA256
    assert f"COPY {PGDG_KEY} {KEYRING}" in _stages(_text())[-1]["lines"]
    install = _final_run("postgresql-16")
    check = f"echo '{qualifier.PGDG_KEY_SHA256}  {KEYRING}' | sha256sum -c -"
    # signed-by trusts the key for the PGDG source only. The suite is the base's Debian release, asserted first.
    source = f"echo 'deb [signed-by={KEYRING}] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main'"
    release = "grep -qx 'VERSION_CODENAME=bookworm' /etc/os-release"
    assert max(install.index(check), install.index(release), install.index(source)) < install.index("apt-get update")


def test_postgresql_16_is_installed_at_the_paths_postgres_bin_checks():
    qualifier = _qualifier()
    installed = inspect.signature(qualifier.postgres_bin).parameters["installed"].default
    assert installed == Path("/usr/lib/postgresql/16/bin")
    assert f'Path("{PGCRYPTO}").is_file()' in inspect.getsource(qualifier.postgres_bin)
    install = _final_run("postgresql-16")
    assert "apt-get install -y --no-install-recommends postgresql-16 postgresql-client-16" in install
    # The build fails instead of shipping an image that would still take the runtime fetch.
    assert f"test -x {installed}/initdb" in install
    assert f"test -f {PGCRYPTO}" in install


def test_packages_only_no_cluster_or_server_and_apt_lists_are_cleaned():
    install = _final_run("postgresql-16")
    order = [
        "apt-get install -y --no-install-recommends postgresql-common",
        "sed -ri 's/#(create_main_cluster) .*$/\\1 = false/' /etc/postgresql-common/createcluster.conf",
        "grep -qx 'create_main_cluster = false' /etc/postgresql-common/createcluster.conf",
        "postgresql-16 postgresql-client-16",
        "rm -rf /var/lib/apt/lists/*",
    ]
    assert [install.index(step) for step in order] == sorted(install.index(step) for step in order)
    assert install.endswith('test -z "$(pg_lsclusters --no-header)"')
    assert not re.search(r"\b(pg_createcluster|pg_ctlcluster|pg_ctl|systemctl|service postgresql)\b", _text())


def test_kustomize_is_checked_against_its_published_hash_before_extraction():
    fetch = _final_run("kustomize")
    assert "/kustomize%2Fv5.4.3/kustomize_v5.4.3_linux_amd64.tar.gz'" in fetch
    check = f"echo '{KUSTOMIZE_SHA256}  /tmp/kustomize.tar.gz' | sha256sum -c -"
    assert fetch.index(check) < fetch.index("tar -xzf /tmp/kustomize.tar.gz")


def test_source_revision_is_baked_from_git_sha():
    metadata, final = (stage["lines"] for stage in _stages(_text()))
    text = " ".join(metadata)
    assert "ARG GIT_SHA=unknown" in metadata
    assert "COPY .git /tmp/verdify-git-metadata" in metadata
    assert "grep -Eq '^[0-9a-f]{40}$'" in text
    assert "> /out/source-revision" in text
    assert "chmod 0444 /out/source-revision" in text
    assert "COPY --from=source-metadata /out/source-revision /etc/verdify/source-revision" in final


def test_build_context_admits_only_the_copy_inputs():
    rules = [line.strip() for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()]
    rules = [rule for rule in rules if rule and not rule.startswith("#")]
    assert all(re.fullmatch(r"!?[A-Za-z0-9._/*-]+", rule) and "**" not in rule for rule in rules), rules
    sources = [
        line.split()[1]
        for stage in _stages(_text())
        for line in stage["lines"]
        if _keyword(line) == "COPY" and not line.split()[1].startswith("--from=")
    ]
    assert sources == [".git", PGDG_KEY]
    # Kaniko skips a file inside an excluded directory, so every parent must be admitted as well.
    for path in (".git", ".git/HEAD", "db", "db/qualification", PGDG_KEY):
        assert not _kaniko_excludes(path, rules), path
    for path in (
        ".env",
        ".git/config",
        ".git/refs/heads/main",
        "api/main.py",
        "ci/validate.Dockerfile",
        "db/schema.sql",
        "db/qualification/268-planner-bootstrap-candidate.sql",
        "scripts/qualify-component-containment.py",
    ):
        assert _kaniko_excludes(path, rules), path
