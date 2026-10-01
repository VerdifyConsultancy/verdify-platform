"""Real shell preparation with local command stand-ins; never database/device access."""

import hashlib
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


roles = load("cnpg-restore-role-parity")
operator = load("cnpg-paired-restore")
SOURCE = (
    "CREATE ROLE verdify;\nALTER ROLE verdify WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS;\n"
)
MANAGEMENT = (
    """CREATE ROLE postgres;
ALTER ROLE postgres WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS;
CREATE ROLE rehearsal_bootstrap;
ALTER ROLE rehearsal_bootstrap WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS;
CREATE ROLE streaming_replica;
ALTER ROLE streaming_replica WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN REPLICATION NOBYPASSRLS;
"""
    + "\n".join(sorted(roles.METRICS_PROFILE))
    + "\n"
)


def test_metrics_exact_posture_membership_retained_separately():
    assert roles.prepare(SOURCE, MANAGEMENT) == SOURCE
    result = roles.verify(SOURCE, SOURCE + MANAGEMENT)
    assert result["separately_enumerated_management_roles"] == sorted(roles.MANAGEMENT)
    assert result["metrics_management_profile_verified"]


@pytest.mark.parametrize(
    "tamper",
    [
        lambda x: x.replace(
            "NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION",
            "SUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION",
        ),
        lambda x: x.replace("pg_monitor TO cnpg_metrics_exporter", "pg_write_all_data TO cnpg_metrics_exporter"),
        lambda x: x + "ALTER ROLE cnpg_metrics_exporter SET search_path TO public;\n",
    ],
)
def test_metrics_privilege_or_settings_drift_refused(tamper):
    with pytest.raises(ValueError):
        roles.prepare(SOURCE, tamper(MANAGEMENT))
    with pytest.raises(ValueError):
        roles.verify(SOURCE, SOURCE + tamper(MANAGEMENT))


def test_source_cannot_mutate_metrics_management_role_or_membership():
    for extra in roles.METRICS_PROFILE:
        with pytest.raises(ValueError):
            roles.prepare(SOURCE + extra + "\n", MANAGEMENT)


@pytest.mark.parametrize(
    "name",
    [
        "../restore-custody-12345678",
        "restore-custody",
        "restore-custody-x",
        "/tmp/restore-custody-12345678",  # noqa: S108 - rejected path fixture
        "restore-custody-12345678;echo",
    ],
)
def test_new_attempt_refuses_unsafe_or_original_path(name):
    with pytest.raises(ValueError):
        operator.stage_path(name, "a" * 64, Path("baseline"), "b" * 64)


def test_new_attempt_requires_original_manifest_and_management_custody():
    assert operator.stage_path("restore-custody-12345678", "a" * 64, Path("baseline"), "b" * 64).endswith(
        "/restore-custody-12345678"
    )
    for args in [
        ("restore-custody-12345678", None, Path("baseline"), "b" * 64),
        ("restore-custody-12345678", "a" * 64, None, "b" * 64),
    ]:
        with pytest.raises(ValueError):
            operator.stage_path(*args)


@pytest.fixture
def shell(tmp_path):
    base = tmp_path / "pgdata"
    base.mkdir()
    stage = base / "restore-custody-12345678"
    stage.mkdir(mode=0o700)
    (stage / "scripts").mkdir()
    (stage / "backups").mkdir()
    for name in ["cnpg-restore-role-parity.py", "cnpg-source-database-acl.py"]:
        (stage / "scripts" / name).write_bytes((ROOT / "scripts" / name).read_bytes())
    management = stage / "management-before.sql"
    management.write_text(MANAGEMENT)
    stem = "verdify-20261001T000000Z"
    (stage / "backups" / f"{stem}.dump").write_bytes(b"fixture")
    (stage / "backups" / f"{stem}.roles.sql").write_text(SOURCE)
    bin = tmp_path / "bin"
    bin.mkdir()
    trace = tmp_path / "calls"
    commands = {
        "id": "echo 26",
        "stat": "echo 26:700",
        "pg_dumpall": 'cat "$MANAGEMENT_FIXTURE"',
        "psql": """printf '%s\\n' "$*" >> "$TRACE"
case "$*" in *' -f '*) exit 77;; esac
cat >/dev/null
""",
    }
    for name, body in commands.items():
        f = bin / name
        f.write_text("#!/bin/sh\n" + body + "\n")
        f.chmod(0o755)
    verify = tmp_path / "verify"
    verify.write_text('#!/bin/sh\nprintf "verdify|verdify\\n"\n')
    verify.chmod(0o755)
    text = (ROOT / "scripts/restore-backup-pair.sh").read_text().replace("/var/lib/postgresql/data", str(base))
    script = tmp_path / "restore.sh"
    script.write_text(text)
    env = {
        **os.environ,
        "PATH": str(bin) + ":" + os.environ["PATH"],
        "BACKUP_DIR": str(stage / "backups"),
        "BACKUP_STEM": stem,
        "PGDATA": str(base / "pgdata"),
        "PGHOST": "/controller/run",
        "PGPORT": "5432",
        "PGDATABASE": "verdify_rehearsal",
        "VERIFY_SCRIPT": str(verify),
        "AUDIT_SQL": "unused",
        "OWNERSHIP_SQL": "unused",
        "OWNER_REPAIR_TEST_SQL": "unused",
        "RESTORED_OWNER_TEST_SQL": "unused",
        "RESTORE_SERVER_MODE": "cnpg",
        "CNPG_RESTORE_CUSTODY": str(stage),
        "RESTORE_WORK_DIR": str(stage / "work"),
        "CNPG_ROLE_HELPER": str(stage / "scripts/cnpg-restore-role-parity.py"),
        "CNPG_ACL_HELPER": str(stage / "scripts/cnpg-source-database-acl.py"),
        "CNPG_MANAGEMENT_BEFORE": str(management),
        "CNPG_MANAGEMENT_BEFORE_SHA256": hashlib.sha256(management.read_bytes()).hexdigest(),
        "MANAGEMENT_FIXTURE": str(management),
        "TRACE": str(trace),
        "TMPDIR": "/readonly-rootfs-unavailable",
    }
    return script, env, stage, trace


def test_cnpg_pre_role_scratch_uses_private_custody_with_unavailable_tmp(shell):
    script, env, stage, trace = shell
    result = subprocess.run(
        ["/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "role replay failed" in result.stderr
    assert (stage / "work/roles.before.sql").read_text() == MANAGEMENT
    assert (stage / "work/roles.replay.sql").read_text() == SOURCE
    assert str(stage / "work/roles.replay.sql") in trace.read_text()
    assert not (stage / "work/source-database-acl.sql").exists()  # fixture stops before first role mutation


def test_existing_scratch_refused_before_any_database_call(shell):
    script, env, stage, trace = shell
    (stage / "work").mkdir()
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and not trace.exists()


def test_symlink_custody_refused_before_any_database_call(shell):
    script, env, stage, trace = shell
    alias = stage.parent / "restore-custody-87654321"
    alias.symlink_to(stage, target_is_directory=True)
    env.update(CNPG_RESTORE_CUSTODY=str(alias), RESTORE_WORK_DIR=str(alias / "work"))
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and not trace.exists()


def test_management_baseline_change_refused_before_role_replay(shell):
    script, env, stage, trace = shell
    modified = stage / "modified.sql"
    modified.write_text(MANAGEMENT.replace("CREATE ROLE streaming_replica;", "CREATE ROLE unexpected;"))
    env["MANAGEMENT_FIXTURE"] = str(modified)
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and "management predecessor semantic drift" in result.stderr
    assert not (stage / "work/roles.replay.sql").exists()
    assert " -f " not in trace.read_text()


def test_standalone_keeps_private_tmpdir_and_server_cleanup(shell, tmp_path):
    script, env, stage, trace = shell
    scratch = tmp_path / "standalone-scratch"
    scratch.mkdir()
    bin_path = Path(env["PATH"].split(":")[0])
    for name in ("getent", "initdb", "pg_ctl"):
        command = bin_path / name
        command.write_text('#!/bin/sh\nprintf "%s %s\\n" "' + name + '" "$*" >> "$TRACE"\n')
        command.chmod(0o755)
    env.update(RESTORE_SERVER_MODE="standalone", TMPDIR=str(scratch))
    result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and "role replay failed" in result.stderr
    work_dirs = list(scratch.iterdir())
    assert len(work_dirs) == 1 and work_dirs[0].stat().st_mode & 0o777 == 0o700
    assert (work_dirs[0] / "roles.replay.sql").read_text() == SOURCE.removeprefix("CREATE ROLE verdify;\n")
    assert "-m fast -w stop" in trace.read_text()
    assert not (stage / "work").exists()


def test_random_restrict_tokens_do_not_change_management_semantics():
    first = "\\restrict first\n" + MANAGEMENT + "\\unrestrict first\n"
    second = "\\restrict second\n" + MANAGEMENT + "\\unrestrict second\n"
    assert roles.canonical(first) == roles.canonical(second)
    assert roles.prepare(SOURCE, first) == SOURCE
    assert roles.verify(SOURCE, SOURCE + second)["metrics_management_profile_verified"]
