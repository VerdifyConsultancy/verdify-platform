"""Initial-only password lifecycle; execution never grants rotation authority."""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "six_bootstrap", Path(__file__).resolve().parents[1] / "scripts/bootstrap-six-runtime-roles.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("DB_ADMIN_USER", "verdify")
    monkeypatch.setenv("DB_ADMIN_PASSWORD", "owner-private-fixture")
    for index, duty in enumerate(module.DUTIES):
        monkeypatch.setenv(duty.upper() + "_DB_PASSWORD", ("A_" + str(index) + "-").ljust(64, "z"))
    return monkeypatch


def executor(monkeypatch, *, absent=True, denied=None):
    calls = []
    state = {"verdify_" + d + "_runtime_login": absent for d in module.DUTIES}

    def run(user, password, commands, **kwargs):
        calls.append((user, commands, kwargs))
        if "json_object_agg" in commands[0]:
            return json.dumps(state)
        if user == denied:
            raise module.BootstrapError("authentication refused")
        return "t"

    monkeypatch.setattr(module, "psql", run)
    return calls


def test_initial_only_transaction_and_exact_six_targets(configured):
    calls = executor(configured)
    module.bootstrap()
    installs = [c for c in calls if c[2].get("transactional")]
    assert len(installs) == 1
    _, commands, kwargs = installs[0]
    assert "LOCK TABLE pg_catalog.pg_authid" in commands[0]
    assert "IS DISTINCT FROM TRUE" in commands[0]
    assert "admin_option" in commands[0]
    assert commands[1:] == ["\\password verdify_" + d + "_runtime_login" for d in module.DUTIES]
    assert all(
        "api_runtime_login" not in c and "ingestor_runtime_login" not in c and "mcp_runtime_login" not in c
        for c in commands[1:]
    )
    for duty in module.DUTIES:
        secret = module.os.environ[duty.upper() + "_DB_PASSWORD"]
        assert secret not in str(commands)
        assert kwargs["password_input"].count(secret) == 2


def test_existing_verifiers_authenticate_without_reinstallation(configured):
    calls = executor(configured, absent=False)
    module.bootstrap()
    assert not any(c[2].get("transactional") for c in calls)
    assert {c[0] for c in calls if c[0] != "verdify"} == {"verdify_" + d + "_runtime_login" for d in module.DUTIES}


def test_mismatched_existing_secret_cannot_rotate(configured):
    calls = executor(configured, absent=False, denied="verdify_planner_runtime_login")
    with pytest.raises(module.BootstrapError):
        module.bootstrap()
    assert not any(c[2].get("transactional") for c in calls)


@pytest.mark.parametrize("bad", ["", "x" * 63, "x" * 65, "'" * 64, "\\" * 64])
def test_unsafe_password_shape_has_no_database_effect(configured, bad):
    configured.setenv("PLANNER_DB_PASSWORD", bad)
    calls = executor(configured)
    with pytest.raises(module.BootstrapError):
        module.bootstrap()
    assert calls == []


def test_duplicate_credentials_have_no_database_effect(configured):
    configured.setenv("PLANNER_DB_PASSWORD", module.os.environ["GRAFANA_DB_PASSWORD"])
    calls = executor(configured)
    with pytest.raises(module.BootstrapError):
        module.bootstrap()
    assert calls == []


def test_real_psql_boundary_keeps_values_out_of_argv_and_errors(configured):
    for name, value in {"DB_HOST": "db", "DB_PORT": "5432", "DB_NAME": "fixture"}.items():
        configured.setenv(name, value)
    secret = "private-password-fixture"
    observed = {}

    def run(args, **kwargs):
        observed.update(args=args, kwargs=kwargs)
        return type("Result", (), {"returncode": 1, "stdout": secret})()

    configured.setattr(module.subprocess, "run", run)
    with pytest.raises(module.BootstrapError, match="database bootstrap operation refused") as error:
        module.psql("verdify", secret, ["\\password verdify_planner_runtime_login"], password_input=secret)
    assert secret not in str(observed["args"])
    assert observed["kwargs"]["env"]["PGPASSWORD"] == secret
    assert observed["kwargs"]["stderr"] == module.subprocess.DEVNULL
    assert secret not in str(error.value)


def test_mixed_existing_and_missing_never_reinstalls_existing(configured):
    calls = executor(configured)
    original = module.psql

    def partial(user, password, commands, **kwargs):
        if "json_object_agg" in commands[0]:
            return json.dumps({"verdify_" + d + "_runtime_login": d != "grafana" for d in module.DUTIES})
        return original(user, password, commands, **kwargs)

    configured.setattr(module, "psql", partial)
    module.bootstrap()
    ((_, commands, kwargs),) = [c for c in calls if c[2].get("transactional")]
    assert "\\password verdify_grafana_runtime_login" not in commands
    assert module.os.environ["GRAFANA_DB_PASSWORD"] not in kwargs["password_input"]


def test_native_hook_order_and_exact_secret_custody_contract():
    import yaml

    root = Path(__file__).resolve().parents[1]
    job = yaml.safe_load(
        (root / "deploy/k8s/components/runtime-role-boundary/six-workload-bootstrap-job.yaml").read_text()
    )
    assert job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    assert job["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "3"
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    container = pod["containers"][0]
    assert container["command"] == ["python3", "/scripts/bootstrap-six-runtime-roles.py"]
    assert container["image"] == "ghcr.io/verdifyconsultancy/verdify-migrate"
    refs = {
        e["name"]: e["valueFrom"]["secretKeyRef"]
        for e in container["env"]
        if e.get("valueFrom", {}).get("secretKeyRef")
    }
    for duty in module.DUTIES:
        assert refs[duty.upper() + "_DB_PASSWORD"] == {
            "name": "verdify-" + duty.replace("_", "-") + "-runtime-db",
            "key": "password",
        }
    assert set(refs) == {"DB_ADMIN_PASSWORD", *(d.upper() + "_DB_PASSWORD" for d in module.DUTIES)}


@pytest.mark.parametrize("answer", ["f", "", "NULL"])
def test_missing_or_unknown_prerequisite_cannot_reach_any_password_command(configured, answer):
    calls = []

    def refuse(user, password, commands, **kwargs):
        calls.append((commands, kwargs))
        return answer

    configured.setattr(module, "psql", refuse)
    with pytest.raises(module.BootstrapError, match="immutable migration/seal prerequisite"):
        module.bootstrap()
    assert len(calls) == 1
    assert not calls[0][1].get("transactional")
    assert not any("\\password" in c for c in calls[0][0])


def test_guard_sql_requires_both_exact_receipts_and_null_role_rows_fail_closed():
    sql = module.sealed_sql()
    assert "count(*)=2" in sql
    for duty in ("api", "ingestor"):
        assert "r.login_name='verdify_" + duty + "_runtime_login'" in sql
        assert "decode('" + module.PREDECESSORS[duty] + "','hex')" in sql
    assert "bool_and(COALESCE((" in module.admin_role_contract_sql()
    root = Path(__file__).resolve().parents[1]
    migration = (root / "db/migrations/268-six-runtime-workload-role-boundaries.sql").read_bytes()
    import hashlib

    assert hashlib.sha256(migration).hexdigest() == module.MIGRATION_SHA


def test_bootstrap_requires_exact_facility_status_successor_after_atomic_migration():
    import hashlib

    root = Path(__file__).resolve().parents[1]
    migration = (root / "db/migrations/270-facility-safe-ops-projection.sql").read_bytes()
    assert hashlib.sha256(migration).hexdigest() == module.OPS_MIGRATION_SHA
    assert "seq=270" in module.sealed_sql()
    assert module.OPS_MIGRATION_SHA in module.sealed_sql()


def test_successor_bootstrap_matches_actual_rollback_qualification():
    """Changing source, seals or migration bytes requires real DB requalification."""
    import hashlib

    root = Path(__file__).resolve().parents[1]
    receipt = json.loads((root / "tests/fixtures/six-runtime-bootstrap-successor-receipt.json").read_text())

    def digest(value):
        return hashlib.sha256(value).hexdigest()

    successor = json.loads((root / "tests/fixtures/six-runtime-bootstrap-274-receipt.json").read_text())
    # Preserve the original independently qualified 270/273 predicate/receipt.
    assert receipt["helper_sha256"] == successor["predecessor_helper_sha256"]
    assert receipt["sealed_sql_sha256"] == digest(module.sealed_sql_270_273().encode())
    assert successor["helper_sha256"] == "b10f191509b7a5cbbfaf7354965ad8725e85ca4de5b0d254b01cce242ed79bfe"
    assert receipt["role_contract_sql_sha256"] == digest(module.admin_role_contract_sql().encode())
    assert receipt["mutations_committed"] is False
    assert receipt["password_commands_executed"] is False
    for seq, filename, expected in module.SUCCESSOR_MIGRATIONS:
        assert receipt["migration_sha256"][str(seq)] == expected
        assert digest((root / "db/migrations" / filename).read_bytes()) == expected
    stages = {row["stage"]: row for row in receipt["stages"]}
    for name, seq in (
        ("current270", 270),
        ("successor273", 273),
        ("successor273_restored", 273),
        ("current270_restored", 270),
    ):
        assert stages[name]["sealed"] is True
        assert stages[name]["role_contract"] is True
        assert stages[name]["ledger"] == seq
    # The prior source really fails after final migration, while the successor
    # remains unavailable at either intermediate stage despite valid roles.
    assert stages["successor273"]["original_sealed"] is False
    for name in ("successor271", "successor272"):
        assert stages[name]["sealed"] is False
        assert stages[name]["role_contract"] is True
    negatives = {
        "wrong_273_hash",
        "wrong_271_hash",
        "wrong_273_filename",
        "wrong_273_stamp",
        "later_274_ledger",
        "wrong_api_receipt",
        "wrong_mcp_receipt",
        "extra_planner_reader_capability",
        "missing_grafana_reader_capability",
        "public_reader_capability",
        "raw_grafana_ledger_capability",
        "unsafe_planner_membership",
    }
    assert len(receipt["stages"]) == len(negatives) + 6
    assert all(stages[name]["sealed"] is False for name in negatives)
    assert stages["unsafe_planner_membership"]["role_contract"] is False
    assert stages["current270_restored"]["original_sealed"] is True


def test_physical_successor_bootstrap_matches_actual_rollback_qualification():
    import hashlib

    root = Path(__file__).resolve().parents[1]
    receipt = json.loads((root / "tests/fixtures/six-runtime-bootstrap-274-receipt.json").read_text())

    def digest(value):
        return hashlib.sha256(value).hexdigest()

    assert receipt["helper_sha256"] == "b10f191509b7a5cbbfaf7354965ad8725e85ca4de5b0d254b01cce242ed79bfe"
    assert receipt["sealed_sql_sha256"] == digest(module.sealed_sql_270_274().encode())
    assert receipt["role_contract_sql_sha256"] == digest(module.admin_role_contract_sql().encode())
    seq, filename, expected = module.PHYSICAL_MIGRATION
    assert seq == 274 and receipt["migration_sha256"] == expected
    assert digest((root / "db/migrations" / filename).read_bytes()) == expected
    assert receipt["mutations_committed"] is False
    assert receipt["password_commands_executed"] is False
    stages = {r["stage"]: r for r in receipt["stages"]}
    for stage in ("current273", "successor274", "successor274_restored", "current273_restored"):
        assert stages[stage]["sealed"] is True
        assert stages[stage]["combined_qualified"] is True
        # Execute the actual caller concatenation, not a string-only assertion.
        assert stages[stage]["precedence_appended_false"] is False
    assert stages["unstamped274"]["sealed"] is False
    assert stages["successor274"]["legacy_sealed"] is False
    for stage in receipt["negative_stages"]:
        assert stages[stage]["sealed"] is False
        assert stages[stage]["combined_qualified"] is False
    fingerprints = {
        stages[s]["ingestor_capability_sha256"]
        for s in ("current273", "unstamped274", "successor274", "successor274_restored", "current273_restored")
    }
    assert len(fingerprints) == 1
    for trial in range(1, 4):
        r = stages[f"ingestor_identity_emulation_{trial}"]
        assert r["identity"] is True and r["attested"] is True
        assert r["session_user"] == r["current_user"] == "verdify_ingestor_runtime_login"
    assert receipt["actual_tcp_qualification"] is False
    assert receipt["original_false_cause"] == "unresolved; exact-prefix and independent reruns passed"


def test_lighting_successor_bootstrap_matches_actual_rollback_qualification():
    """New source authority requires an authentic successor and refusal receipt."""
    import hashlib

    root = Path(__file__).resolve().parents[1]
    receipt = json.loads((root / "tests/fixtures/six-runtime-bootstrap-275-receipt.json").read_text())

    def digest(value):
        return hashlib.sha256(value).hexdigest()

    predecessor = json.loads((root / "tests/fixtures/six-runtime-bootstrap-274-receipt.json").read_text())
    assert receipt["predecessor_helper_sha256"] == predecessor["helper_sha256"]
    assert receipt["helper_sha256"] == digest((root / "scripts/bootstrap-six-runtime-roles.py").read_bytes())
    assert receipt["sealed_sql_sha256"] == digest(module.sealed_sql().encode())
    assert receipt["legacy_sealed_sql_sha256"] == predecessor["sealed_sql_sha256"]
    assert receipt["legacy_sealed_sql_sha256"] == digest(module.sealed_sql_270_274().encode())
    assert receipt["role_contract_sql_sha256"] == predecessor["role_contract_sql_sha256"]
    assert receipt["role_contract_sql_sha256"] == digest(module.admin_role_contract_sql().encode())
    seq, filename, expected = module.LIGHTING_MIGRATION
    assert seq == 275 and receipt["migration_sha256"] == expected
    assert digest((root / "db/migrations" / filename).read_bytes()) == expected
    assert receipt["successor_catalog_digests"] == module.SUCCESSOR_275
    assert receipt["mutations_committed"] is False
    assert receipt["password_commands_executed"] is False
    assert receipt["actual_tcp_qualification"] is False
    assert receipt["independent_post_rollback_exact"] is True
    assert receipt["native_authority_unchanged"] is True
    assert receipt["full_relation_vectors_exact"] is True
    assert receipt["full_relation_count"] == 6522
    assert len(receipt["private_proof_sha256"]) == 64
    assert receipt["historical_failed_attempts"][0]["qualified"] is False
    assert receipt["historical_failed_attempts"][1]["mutable_qualification_sql_executed"] is False
    stages = {r["stage"]: r for r in receipt["stages"]}
    for name in ("current274", "successor275", "successor275_restored", "current274_restored"):
        assert stages[name]["sealed"] is True
        assert stages[name]["combined_qualified"] is True
        assert stages[name]["role_contract"] is True
        assert stages[name]["precedence_appended_false"] is False
    assert stages["current274"]["legacy_sealed"] is True
    assert stages["current274_restored"]["legacy_sealed"] is True
    assert stages["successor275"]["legacy_sealed"] is False
    assert stages["unstamped275"]["sealed"] is False
    negatives = {
        "wrong_275_hash",
        "wrong_275_filename",
        "wrong_275_stamp",
        "later_276_ledger",
        "wrong_api_receipt",
        "wrong_ingestor_receipt",
        "wrong_mcp_receipt",
        "missing_jit_config",
        "unsafe_planner_membership",
        "extra_planner_physical_read",
    }
    assert set(receipt["negative_stages"]) == negatives
    for name in negatives:
        assert stages[name]["combined_qualified"] is False
    assert stages["unsafe_planner_membership"]["role_contract"] is False
    assert len(stages) == len(negatives) + 5
