"""Exact phase continuation and native PG16 grantor behavior; no estate access."""

import hashlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cnpg_roles", ROOT / "scripts/cnpg-restore-role-parity.py")
roles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roles)
MANAGEMENT = (
    "CREATE ROLE postgres;\nALTER ROLE postgres WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS;\n"
    + "\n".join(sorted(roles.METRICS_PROFILE))
    + "\n"
)
SOURCE = "CREATE ROLE verdify;\nALTER ROLE verdify WITH SUPERUSER INHERIT CREATEROLE CREATEDB LOGIN REPLICATION BYPASSRLS;\nCREATE ROLE agent_ro;\nALTER ROLE agent_ro WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB NOLOGIN NOREPLICATION NOBYPASSRLS;\nCREATE ROLE agent;\nALTER ROLE agent WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS;\nALTER ROLE agent SET search_path TO 'pg_catalog';\nGRANT agent_ro TO agent WITH INHERIT TRUE GRANTED BY verdify;\n"


def custody():
    before = MANAGEMENT.encode()
    replay = roles.legacy_prepare(SOURCE, MANAGEMENT).encode()
    current = (SOURCE.removesuffix(SOURCE.splitlines(keepends=True)[-1]) + MANAGEMENT).encode()
    first = next(i for i, line in enumerate(replay.decode().splitlines(), 1) if line.startswith("GRANT "))
    error = f'psql:/var/lib/postgresql/data/restore-custody-20261001r3/work/roles.replay.sql:{first}: ERROR:  permission denied to grant privileges as role "verdify"\nDETAIL:  The grantor must have the ADMIN option on role "agent_ro".\n'.encode()
    descriptor = {"stage_name": "restore-custody-20261001r3", "manifest_sha256": "a" * 64}
    for key, raw in zip(
        ("current_sha256", "before_sha256", "replay_sha256", "error_sha256"),
        (current, before, replay, error),
        strict=True,
    ):
        descriptor[key] = hashlib.sha256(raw).hexdigest()
    return descriptor, current, before, replay, error


def test_prefix_emits_original_membership_only_in_exact_grantor_transaction():
    d, current, before, replay, error = custody()
    result = roles.continue_prefix(SOURCE, current.decode(), MANAGEMENT, d, current, before, replay, error)
    assert (
        result
        == "BEGIN;\nSET SESSION AUTHORIZATION verdify;\n"
        + SOURCE.splitlines()[-1]
        + "\nRESET SESSION AUTHORIZATION;\nCOMMIT;\n"
    )
    assert "ADMIN TRUE" not in result and "CREATE ROLE" not in result


@pytest.mark.parametrize("tamper", ["membership", "settings", "management", "failure", "replay", "hash"])
def test_changed_partial_boundary_never_grants_authority(tamper):
    d, current, before, replay, error = custody()
    if tamper == "membership":
        current += SOURCE.splitlines(keepends=True)[-1].encode()
    elif tamper == "settings":
        current = current.replace(b"'pg_catalog'", b"'public'")
    elif tamper == "management":
        current = current.replace(b"pg_monitor TO", b"pg_write_all_data TO")
    elif tamper == "failure":
        error = error.replace(b"permission denied", b"connection lost")
    elif tamper == "replay":
        replay += b"GRANT agent_ro TO postgres WITH INHERIT TRUE GRANTED BY verdify;\n"
    else:
        d["current_sha256"] = "b" * 64
    if tamper != "hash":  # Even externally captured new hashes cannot relax the source/failure boundary.
        for key, raw in zip(
            ("current_sha256", "before_sha256", "replay_sha256", "error_sha256"),
            (current, before, replay, error),
            strict=True,
        ):
            d[key] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        roles.continue_prefix(SOURCE, current.decode(), MANAGEMENT, d, current, before, replay, error)


@pytest.mark.parametrize(
    "grant",
    [
        "GRANT agent_ro TO agent WITH INHERIT TRUE GRANTED BY postgres;",
        "GRANT agent_ro TO agent;",
        "GRANT agent_ro TO agent WITH ADMIN TRUE GRANTED BY unknown;",
    ],
)
def test_unsupported_or_unknown_source_grantor_refused(grant):
    with pytest.raises(ValueError):
        roles.grant_context(SOURCE[: SOURCE.rfind("GRANT ")] + grant + "\n", memberships_only=True)


def test_native_pg16_exact_grantor_membership_and_atomic_failure(tmp_path):
    directory = os.environ.get("CNPG_TEST_PG_BIN")
    if not directory:
        pytest.skip("set CNPG_TEST_PG_BIN for isolated native PG16 grantor proof")
    pg = Path(directory)
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env["LC_ALL"] = "C"

    def run(args, **kwargs):
        return subprocess.run(args, env=env, text=True, capture_output=True, timeout=60, **kwargs)

    assert (
        run(
            [
                str(pg / "initdb"),
                "-D",
                str(tmp_path / "data"),
                "-U",
                "postgres",
                "--auth-local=trust",
                "--auth-host=reject",
                "--no-locale",
            ]
        ).returncode
        == 0
    )
    started = False
    socket = Path(tempfile.mkdtemp(prefix="c5-gr-"))
    try:
        start = run(
            [
                str(pg / "pg_ctl"),
                "-D",
                str(tmp_path / "data"),
                "-l",
                str(tmp_path / "server.log"),
                "-o",
                f"-k {socket} -p 55478 -c listen_addresses=''",
                "-w",
                "start",
            ]
        )
        assert start.returncode == 0, start.stderr
        started = True

        def query(sql):
            return run(
                [
                    str(pg / "psql"),
                    "-X",
                    "-qAt",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    str(socket),
                    "-p",
                    "55478",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                ],
                input=sql,
            )

        assert (
            query("SELECT current_setting('server_version_num')::int BETWEEN 160000 AND 169999;").stdout.strip() == "t"
        )
        assert query(SOURCE[: SOURCE.rfind("GRANT ")]).returncode == 0
        denied = query(SOURCE.splitlines()[-1])
        assert denied.returncode != 0 and "must have the ADMIN option" in denied.stderr
        denied_as_source = query(roles.grant_context(SOURCE, memberships_only=True))
        assert denied_as_source.returncode != 0 and "must have the ADMIN option" in denied_as_source.stderr
        assert (
            query("GRANT agent_ro TO verdify WITH ADMIN TRUE, INHERIT FALSE, SET FALSE GRANTED BY postgres;").returncode
            == 0
        )
        assert query(roles.grant_context(SOURCE, memberships_only=True)).returncode == 0
        fact = query(
            "SELECT r.rolname,m.rolname,g.rolname,admin_option,inherit_option,set_option FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid JOIN pg_roles m ON m.oid=a.member JOIN pg_roles g ON g.oid=a.grantor WHERE r.rolname='agent_ro' AND m.rolname='agent';"
        )
        assert fact.stdout.strip() == "agent_ro|agent|verdify|f|t|t"
        assert query("SELECT current_user,session_user;").stdout.strip() == "postgres|postgres"
        restricted = query("REVOKE ADMIN OPTION FOR agent_ro FROM verdify GRANTED BY postgres RESTRICT;")
        assert restricted.returncode != 0 and "dependent privileges" in restricted.stderr
        assert query("REVOKE ADMIN OPTION FOR agent_ro FROM verdify GRANTED BY postgres CASCADE;").returncode == 0
        assert (
            query(
                "SELECT count(*) FROM pg_auth_members a JOIN pg_roles m ON m.oid=a.member WHERE m.rolname='agent';"
            ).stdout.strip()
            == "0"
        )
        assert query("REVOKE agent_ro FROM verdify GRANTED BY postgres CASCADE;").returncode == 0
        assert (
            query("GRANT agent_ro TO verdify WITH ADMIN TRUE, INHERIT FALSE, SET FALSE GRANTED BY postgres;").returncode
            == 0
        )
        assert query("CREATE ROLE another;").returncode == 0
        atomic = query(
            "BEGIN; SET SESSION AUTHORIZATION verdify; GRANT agent_ro TO another WITH INHERIT TRUE GRANTED BY verdify; GRANT missing_role TO another WITH INHERIT TRUE GRANTED BY verdify; COMMIT;"
        )
        assert atomic.returncode != 0
        assert (
            query(
                "SELECT count(*) FROM pg_auth_members a JOIN pg_roles m ON m.oid=a.member WHERE m.rolname='another';"
            ).stdout.strip()
            == "0"
        )
    finally:
        if started:
            run([str(pg / "pg_ctl"), "-D", str(tmp_path / "data"), "-m", "immediate", "-w", "stop"])
        shutil.rmtree(socket)


def mapping():
    return roles.bootstrap_mapping(
        {"roles": {"10": "verdify"}}, SOURCE, {"oid": 10, "name": "postgres", "superuser": True}
    )


def test_explicit_profile_retains_raw_difference_and_flags_without_extra_admin():
    d, current, before, replay, error = custody()
    result = roles.continue_prefix(
        SOURCE, current.decode(), MANAGEMENT, d, current, before, replay, error, mapping=mapping()
    )
    translated = SOURCE.splitlines()[-1].removesuffix("verdify;") + "postgres;"
    assert translated in result and "SET SESSION AUTHORIZATION postgres;" in result
    restored = SOURCE.replace(SOURCE.splitlines()[-1], translated) + MANAGEMENT
    report = roles.verify(SOURCE, restored, mapping=mapping())
    assert report["role_byte_parity"] is False
    assert report["role_posture_settings_and_membership_options_equal"]
    assert report["raw_membership_differences"] == [{"source": SOURCE.splitlines()[-1], "target": translated}]
    with pytest.raises(ValueError):
        roles.verify(SOURCE, restored)


@pytest.mark.parametrize("field,value", [("oid", 11), ("name", "verdify"), ("superuser", False)])
def test_native_target_bootstrap_fact_is_mandatory(field, value):
    identity = {"oid": 10, "name": "postgres", "superuser": True}
    identity[field] = value
    with pytest.raises(ValueError):
        roles.bootstrap_mapping({"roles": {"10": "verdify"}}, SOURCE, identity)


def test_source_oid10_identity_and_original_superuser_cannot_be_assumed():
    for witness, source in [
        ({"roles": {"100": "verdify"}}, SOURCE),
        ({"roles": {"10": "postgres"}}, SOURCE),
        ({"roles": {"10": "verdify"}}, SOURCE.replace(" WITH SUPERUSER ", " WITH NOSUPERUSER ")),
    ]:
        with pytest.raises(ValueError):
            roles.bootstrap_mapping(witness, source, {"oid": 10, "name": "postgres", "superuser": True})


def translated_witnesses():
    import copy

    from test_cnpg_restore_qualification import witnesses

    source, target = witnesses()
    source["roles"] = {"10": "verdify", "20": "verdify_mcp_runtime", "30": "verdify_mcp_runtime_login"}
    target["roles"] = {
        "10": "postgres",
        "100": "verdify",
        "200": "verdify_mcp_runtime",
        "300": "verdify_mcp_runtime_login",
    }
    target["bootstrap_grantor_profile"] = roles.BOOTSTRAP_PROFILE
    target["bootstrap_identity"] = {"oid": 10, "name": "postgres", "superuser": True}
    for login in source["boundaries"]:
        if login != "verdify_mcp_runtime_login":
            member = (
                f"member|role={login.removesuffix('_login')}|member={login}|grantor=verdify|admin=f|inherit=t|set=t"
            )
            source["boundaries"][login]["raw_entries"].append(member)
            source["boundaries"][login]["semantic_entries"].append(member)
            target["boundaries"][login]["raw_entries"].append(member.replace("grantor=verdify", "grantor=postgres"))
            target["boundaries"][login]["semantic_entries"].append(
                member.replace("grantor=verdify", "grantor=postgres")
            )
        else:
            source["boundaries"][login]["raw_entries"].append("member|20|30|10|f|t|t")
            target["boundaries"][login]["raw_entries"].append("member|200|300|10|f|t|t")
        for snapshot in [source, target]:
            data = snapshot["boundaries"][login]
            data["native"] = hashlib.sha256("\n".join(sorted(data["raw_entries"])).encode()).hexdigest()
    source["seals"] = {
        "ordinary": [
            [login, source["boundaries"][login]["native"]]
            for login in source["boundaries"]
            if login != "verdify_mcp_runtime_login"
        ],
        "mcp": [source["boundaries"]["verdify_mcp_runtime_login"]["native"]],
    }
    target["seals"] = copy.deepcopy(source["seals"])
    return source, target


def load_c0():
    s = importlib.util.spec_from_file_location("c0_bootstrap", ROOT / "scripts/cnpg-c0-restore-qualification.py")
    c0 = importlib.util.module_from_spec(s)
    s.loader.exec_module(c0)
    # This suite's synthetic predecessor is not the frozen estate ec9 witness.
    c0.FROZEN_SOURCE_V2_CATALOG_SHA256 = c0.catalog_sha256([["function", "vision.example()", "d" * 64]])
    return c0


def test_target_witness_retains_raw_mismatch_and_all_original_seals():
    import copy

    c0 = load_c0()
    source, target = translated_witnesses()
    before = copy.deepcopy((source, target))
    result = c0.compare(source, target)
    assert result["physical_semantic_boundaries_equal"] is False
    assert result["semantic_boundaries_equal"] is True
    assert len(result["raw_bootstrap_grantor_differences"]) == 3
    assert (source, target) == before
    sql = c0.emit_sql(target=True, bootstrap_grantor_profile=True)
    assert "'bootstrap_grantor_profile','cnpg-source-bootstrap-grantor-v1'" in sql
    assert "FROM pg_roles WHERE oid=10" in sql
    with pytest.raises(ValueError):
        c0.emit_sql(bootstrap_grantor_profile=True)


@pytest.mark.parametrize(
    "drift",
    [
        "admin",
        "inherit",
        "set",
        "member",
        "acl",
        "owner",
        "source_oid",
        "target_oid",
        "unknown_profile",
        "forged_semantic",
        "old_seal",
    ],
)
def test_target_profile_refuses_every_unlisted_catalog_delta(drift):
    c0 = load_c0()
    source, target = translated_witnesses()
    login = next(iter(c0.boundary.LOGINS))
    data = target["boundaries"][login]
    if drift in ["admin", "inherit", "set", "member"]:
        old, new = {
            "admin": ("admin=f", "admin=t"),
            "inherit": ("inherit=t", "inherit=f"),
            "set": ("set=t", "set=f"),
            "member": (f"member={login}", "member=another"),
        }[drift]
        for field in ["raw_entries", "semantic_entries"]:
            data[field] = [e.replace(old, new) for e in data[field]]
        data["native"] = hashlib.sha256("\n".join(sorted(data["raw_entries"])).encode()).hexdigest()
    elif drift in ["acl", "owner"]:
        target["portable_catalog"][0][2] = "e" * 64
    elif drift == "source_oid":
        source["roles"]["10"] = "postgres"
    elif drift == "target_oid":
        target["bootstrap_identity"]["oid"] = 100
    elif drift == "unknown_profile":
        target["bootstrap_grantor_profile"] = "other"
    elif drift == "forged_semantic":
        data["semantic_entries"] = [e.replace("grantor=postgres", "grantor=verdify") for e in data["semantic_entries"]]
    else:
        target["seals"]["ordinary"][0][1] = "a" * 64
    with pytest.raises(ValueError):
        c0.compare(source, target)


def test_restore_summary_preserves_actual_raw_parity_failure():
    exact = roles.verify(SOURCE, SOURCE + MANAGEMENT)
    assert roles.restore_summary(exact) == "[restore-pair] exact password-free role parity=true"
    selected_mapping = mapping()
    translated = SOURCE.replace("GRANTED BY verdify;", "GRANTED BY postgres;")
    report = roles.verify(SOURCE, translated + MANAGEMENT, mapping=selected_mapping)
    summary = roles.restore_summary(report)
    assert "byte parity=false" in summary
    assert "bootstrap-grantor-equivalence=true" in summary
    assert "retained_grantor_differences=1" in summary
    assert "role parity=true" not in summary
    report["role_posture_settings_and_membership_options_equal"] = False
    with pytest.raises(ValueError):
        roles.restore_summary(report)
