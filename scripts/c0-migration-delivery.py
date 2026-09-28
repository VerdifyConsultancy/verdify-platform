"""Owning-runner C0 delivery: exact contract, atomic transition, readback.

The normal runner delegates before bootstrap when its inventory includes any
C0 migration. There is no per-file fallback and no contract-generation mode.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("c0_boundary_transition", ROOT / "scripts/c0-boundary-transition.py")
transition = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transition)
CORE_SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout = '30s';
SELECT jsonb_build_object(
    'timescale', EXISTS(SELECT 1 FROM pg_catalog.pg_extension WHERE extname='timescaledb'),
    'timescale_version', (SELECT extversion FROM pg_catalog.pg_extension WHERE extname='timescaledb'),
    'core', to_regclass('public.climate') IS NOT NULL
        AND to_regclass('public.setpoint_changes') IS NOT NULL
        AND to_regclass('public.equipment_state') IS NOT NULL);
COMMIT;"""
LEDGER_SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout = '30s';
SELECT coalesce(jsonb_agg(jsonb_build_object('source',source,'filename',filename,
    'seq',seq,'sha256',sha256,'stamp_method',stamp_method) ORDER BY source,filename),'[]')
FROM public.schema_migrations;
COMMIT;"""
SUCCESSOR_249 = "249-observed-minute-runtime-write-grant.sql"
SUCCESSOR_249_SHA256 = "9f1ad8a9c9b721bbfdce8696a59c260a510f90eff34b4a202f99c60e0d9855ab"
SUCCESSOR_249_DIGESTS = {
    "verdify_api_runtime_login": "8b895d1a4dcf403098fcfa8dc9645ed330e7b43ae8b08128456692cd0a89105a",
    "verdify_ingestor_runtime_login": "2556baed8f07bb9d9537b963e7749610004b32814e71666a8d12ae2177908cd2",
}
SUCCESSOR_254 = "254-post-253-ordinary-login-attestation.sql"
SUCCESSOR_254_DIGESTS = {
    "verdify_api_runtime_login": "9b5e6841cebc95cff6020f1a899e65c1f504d927844f66f7045ecfb1eef23451",
    "verdify_ingestor_runtime_login": "52c1d03192df977396e4e61075616ee18ecb66c6b771005261c510980e97adc3",
}
SUCCESSOR_255 = "255-fixed-panel-crop-assignment-lineage.sql"
SUCCESSOR_255_SHA256 = "c9276c02fa7641f37bad82b1d7fb829aba81c143d16d514606c5b6f6b8facd7b"
SUCCESSOR_255_DIGESTS = {
    "verdify_api_runtime_login": "a52e94f2b6fdecf792cfa819a33f6cd4a950d1076fec1895e9870a63b362cf62",
    "verdify_ingestor_runtime_login": "5bdcd842aa593e15f7d33f4f335dfac0278adc62a0ea929b2590880adbf24c8d",
}
SUCCESSOR_256 = "256-experiment-v2-end-study-recovery-completion.sql"
SUCCESSOR_256_SHA256 = "b35ed01468ef505dc46b035b6b3c15ee57ba96d6600183ffd98b9975d4fffe0f"
SUCCESSOR_256_DIGESTS = {
    "verdify_api_runtime_login": "7c8b0d3f8dcfa8552068ca8373e0f394aafb3c27aab1fda72c7eebd3083c904a",
    "verdify_ingestor_runtime_login": "7783f5d743751224ae157fe063941c76e00167a0208cd233150a9b68b06633fa",
}
SUCCESSOR_257 = "257-route-only-crop-band-publication.sql"
SUCCESSOR_257_SHA256 = "48dc31ad941bc55a353e9a3514692f20e64e11806faca6d83a19aa771b0e1ac4"
SUCCESSOR_257_DIGESTS = {
    "verdify_api_runtime_login": "444063bd61ccb69f02888ede5f2c2338d7882b954af7141e267cdb53b4ed9c7e",
    "verdify_ingestor_runtime_login": "86660529322d02ce6e735d329f6c5e320eeb53f9a8b2890a9a285eaf852f88f5",
}
SUCCESSOR_258 = "258-mcp-runtime-inert-roles.sql"
SUCCESSOR_258_SHA256 = "5558c4be0d3624ccd5821d2e0231b4625feaa64a6657e5fe53ca67b79d55e78e"
# Reserving two NOLOGIN roles grants nothing to either existing runtime.  The
# predecessor receipts must remain exact until a separately sealed cutover.
SUCCESSOR_258_DIGESTS = SUCCESSOR_257_DIGESTS
SUCCESSOR_259 = "259-mcp-ordinary-runtime-boundary.sql"
SUCCESSOR_259_SHA256 = "5eb45e7264eb28d05aa30acab76bd571d7a5e7466f51b9a877fcea0beab13054"
SUCCESSOR_259_DIGESTS = {
    "verdify_api_runtime_login": "edb663118ffc9c5fc2a6e00a9525433fdec92faebf071943c5e5feed4bbc5524",
    "verdify_ingestor_runtime_login": "9349738c72983658a23f17ba1435c2fc42e2392ac58c35c34365a93c96345915",
}
SUCCESSOR_259_MCP_DIGEST = "c8b68f940995824e9dfbe6334f7c66ac38952fa2efd9423682dc2b9cd4542ba8"
SUCCESSOR_260 = "260-restore-climate-action-daily-scorecard.sql"
SUCCESSOR_260_SHA256 = "3f1b1d0c7c055db75f008ab171ec505665ec2df14e918c2845dfbce3ff98cb80"
SUCCESSOR_260_DIGESTS = SUCCESSOR_259_DIGESTS
SUCCESSOR_260_MCP_DIGEST = "c61838a50ec877274a1d6a90a4a0b0f7e6d7c879e3f08a6421cefac56aa21fb5"
SUCCESSOR_261 = "261-outcome-kpi-ordinary-acl.sql"
SUCCESSOR_261_SHA256 = "159d67ec9e34ec386a9e8bda5d57fbaf76243c5ce1f11dd276927c9834d86896"
SUCCESSOR_261_DIGESTS = {
    "verdify_api_runtime_login": "d259673dee68b8eefc6e4b164f82412c78587ea5043715e410ea199db4a553c2",
    "verdify_ingestor_runtime_login": "2fe7dfba3f23e1c1b053b8f5d245319072546d93f40f6643902f1bdf4c7e2a97",
}
SUCCESSOR_261_MCP_DIGEST = "116b10bdf81496423026f3c467a9c10aa6b2767867cb428e03271c71a34393fe"
SUCCESSOR_262 = "262-fixed-panel-native-callback-ledger.sql"
SUCCESSOR_262_SHA256 = "6b9ebfb1ee429fac2a31c07253014ccfbc1832e08307f7f0e0aad867fc46020e"
SUCCESSOR_262_DIGESTS = {
    "verdify_api_runtime_login": "fcedb02292df921dcc0f106e41ee53338065a16c6b8ed8e58780c544bd03551b",
    "verdify_ingestor_runtime_login": "1ee6b4aa40eb9e56c7ab90ebb18cc6c2cb094a0e5d67092c8c406f72fc321fbc",
}
SUCCESSOR_262_MCP_DIGEST = "c34f6091839412a8578c1061a0bddae987fe65d8cf8cf5551fddca63924cfff3"
SUCCESSOR_263 = "263-scorecard-fixed-minute-grid.sql"
SUCCESSOR_263_SHA256 = "1c5dc4e421d8db4f50e0fba983b0573e5291a8a0a787eb580adc0fc63fe2debc"
SUCCESSOR_263_DIGESTS = SUCCESSOR_262_DIGESTS
SUCCESSOR_263_MCP_DIGEST = "83d71d757200e93d288eb005ef09449cd6737c73164eb042d511725446cf4382"


class DeliveryError(ValueError):
    """A locally authored, value-free diagnostic safe for the job log."""


def require(ok, message):
    if not ok:
        raise DeliveryError(message)


def reviewed_post_254(later, files=None):
    """Admit only the reviewed, ordered receipt successors after 254."""
    successors = [name for name in later if int(name[:3]) > 254]
    reviewed = (
        SUCCESSOR_255,
        SUCCESSOR_256,
        SUCCESSOR_257,
        SUCCESSOR_258,
        SUCCESSOR_259,
        SUCCESSOR_260,
        SUCCESSOR_261,
        SUCCESSOR_262,
        SUCCESSOR_263,
    )
    require(successors == list(reviewed[: len(successors)]), "unreviewed post-254 receipt successor")
    if successors:
        require(SUCCESSOR_254 in later, "unreviewed post-254 receipt successor")
    if successors and files is not None:
        for name, sha in zip(
            reviewed,
            (
                SUCCESSOR_255_SHA256,
                SUCCESSOR_256_SHA256,
                SUCCESSOR_257_SHA256,
                SUCCESSOR_258_SHA256,
                SUCCESSOR_259_SHA256,
                SUCCESSOR_260_SHA256,
                SUCCESSOR_261_SHA256,
                SUCCESSOR_262_SHA256,
                SUCCESSOR_263_SHA256,
            ),
            strict=True,
        ):
            if name in successors:
                require(files.get(name) == sha, f"reviewed {name[:3]} successor source drift")


def inventory(directory):
    require(directory.is_dir(), "migration directory unavailable")
    files = {}
    for path in sorted(directory.glob("*.sql")):
        require(path.is_file() and not path.is_symlink(), "migration must be a regular file")
        require(re.fullmatch(r"[0-9][a-zA-Z0-9._-]*\.sql", path.name), "invalid migration filename")
        raw = path.read_bytes()
        installed = ROOT / "db/migrations" / path.name
        require(installed.is_file() and raw == installed.read_bytes(), "inventory differs from image source")
        files[path.name] = hashlib.sha256(raw).hexdigest()
    cohort = {name: sha for name, sha in files.items() if re.match(r"24[1-7]", name)}
    require(cohort == transition.MIGRATIONS, "complete exact C0 inventory required")
    transition.checked_sources()
    return files


def load_contract(environment, *, plan):
    filename = environment.get("VERDIFY_C0_BOUNDARY_CONTRACT", "")
    pin = environment.get("VERDIFY_C0_BOUNDARY_CONTRACT_SHA256", "")
    if plan and not filename and not pin:
        return None, None
    require(filename and pin, "C0 requires a reviewed contract and independent hash pin")
    require(transition.is_hash(pin), "invalid contract hash pin")
    contract, actual = transition.boundary.read_snapshot(Path(filename))
    require(actual == pin, "contract does not match reviewed hash pin")
    transition.validate(contract)
    return contract, actual


def psql(sql, environment):
    for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASS"):
        require(environment.get(key), "required database binding missing")
    env = dict(environment)
    env["PGPASSWORD"] = env["DB_PASS"]
    env["PGOPTIONS"] = (
        "-c statement_timeout=15min -c lock_timeout=30s "
        "-c idle_in_transaction_session_timeout=5min -c search_path=pg_catalog,public,pg_temp"
    )
    command = [
        "psql",
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-v",
        "VERBOSITY=verbose",
        "-h",
        env["DB_HOST"],
        "-p",
        env.get("DB_PORT", "5432"),
        "-U",
        env["DB_USER"],
        "-d",
        env["DB_NAME"],
    ]
    result = subprocess.run(command, input=sql, env=env, text=True, capture_output=True, timeout=1200)
    if result.returncode:
        # Server errors can contain row values, SQL context or credentials.
        # Preserve only a syntactically bounded SQLSTATE, never raw stdout/stderr.
        state = re.search(r"(?:ERROR|FATAL):\s+([A-Z0-9]{5}):", result.stderr)
        raise DeliveryError("database command refused" + (f" (SQLSTATE {state.group(1)})" if state else ""))
    return result.stdout.strip()


def ledger_rows(environment):
    raw = psql(LEDGER_SQL, environment)
    try:
        rows = json.loads(raw)
    except (ValueError, RecursionError):
        raise DeliveryError("invalid ledger readback") from None
    require(isinstance(rows, list), "invalid ledger readback")
    result = {}
    for row in rows:
        require(
            isinstance(row, dict) and set(row) == {"source", "filename", "seq", "sha256", "stamp_method"},
            "invalid ledger row",
        )
        require(isinstance(row["source"], str) and isinstance(row["filename"], str), "invalid ledger identity")
        key = (row["source"], row["filename"])
        require(key not in result, "duplicate ledger identity")
        result[key] = row
    return result


def verify_inventory_ledger(files, rows, *, after=False, version=transition.VERSION):
    migrations = transition.release_migrations(version)
    pending = 0
    for name, sha in files.items():
        row = rows.get(("db/migrations", "db/migrations/" + name))
        if name in migrations:
            if row is None:
                pending += 1
                require(not after, "C0 stamp missing after transaction")
            else:
                require(
                    row["sha256"] == sha and row["seq"] == int(name[:3]) and row["stamp_method"] == "runner",
                    "C0 stamp is not exact",
                )
        else:
            require(row is not None, "pending migration outside the qualified C0 bundle")
            require(
                row["sha256"] == sha or (row["sha256"] is None and row["stamp_method"] == "baseline"),
                "prior image/ledger source mismatch",
            )
    require(pending in (0, len(migrations)), "partial C0 release must not resume per-file")
    return pending


def successor_probe(contract):
    migrations = transition.release_migrations(contract["version"])
    member = (
        "source='db/migrations' AND filename IN ("
        + ",".join(transition.literal("db/migrations/" + name) for name in migrations)
        + ")"
    )
    return f"""BEGIN READ ONLY;
SET LOCAL search_path = pg_catalog, pg_temp;
SET LOCAL statement_timeout = '30s';
DO $c0_delivery_readback$
DECLARE v_digest text;
BEGIN
    {transition.function_guard(contract["version"])}
    {transition.ledger_shape_guard()}
    IF ({transition.ledger_digest_sql("NOT (" + member + ")")})
        IS DISTINCT FROM '{contract["predecessor_ledger_sha256"]}'
        OR (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2 THEN
        RAISE EXCEPTION 'C0 delivery readback refuses predecessor ledger or receipt count drift';
    END IF;
    {transition.digest_guard(contract["after"], contract["after"])}
END;
$c0_delivery_readback$;
COMMIT;"""


def completed_resource_successor(files, rows, contract):
    """Admit the first post-C0 migration only after all nine exact C0 stamps."""
    if contract is None or contract["version"] != transition.RESOURCE_VERSION or SUCCESSOR_249 not in files:
        return False
    migrations = transition.release_migrations(contract["version"])
    if not all(rows.get(("db/migrations", "db/migrations/" + name)) is not None for name in migrations):
        return False
    require(files[SUCCESSOR_249] == SUCCESSOR_249_SHA256, "reviewed successor source drift")
    prior = {name: sha for name, sha in files.items() if int(name[:3]) < 249}
    require(verify_inventory_ledger(prior, rows, version=contract["version"]) == 0, "C0 predecessor incomplete")
    require(
        all(rows.get(("db/migrations", "db/migrations/" + name)) is not None for name in prior),
        "pending migration outside reviewed successor",
    )
    row = rows.get(("db/migrations", "db/migrations/" + SUCCESSOR_249))
    if row is not None:
        require(
            row["sha256"] == SUCCESSOR_249_SHA256 and row["seq"] == 249 and row["stamp_method"] == "runner",
            "successor stamp is not exact",
        )
    return True


def post_249_inventory(files, rows):
    """Admit only an ordered, exact ledger prefix of ordinary successors."""
    later = {name: sha for name, sha in files.items() if int(name[:3]) >= 250}
    for name in later:
        require(re.fullmatch(r"[0-9]{3}[a-zA-Z]?-[a-zA-Z0-9._-]+\.sql", name), "invalid post-249 migration filename")
    require(
        not any(
            source == "db/migrations"
            and row["seq"] is not None
            and row["seq"] >= 250
            and filename.removeprefix("db/migrations/") not in later
            for (source, filename), row in rows.items()
        ),
        "post-249 ledger source absent from inventory",
    )
    pending = False
    for name, sha in sorted(later.items()):
        row = rows.get(("db/migrations", "db/migrations/" + name))
        if row is None:
            pending = True
            continue
        require(not pending, "post-249 ledger has an out-of-order stamp")
        require(
            row["sha256"] == sha and row["seq"] == int(name[:3]) and row["stamp_method"] == "runner",
            "post-249 stamp is not exact",
        )
    return later


def verify_post_249(contract, environment, *, later=()):
    """The frozen C0 after digest legitimately changes at the reviewed grant."""
    reviewed_post_254(later)
    migrations = transition.release_migrations(contract["version"])
    excluded = ["db/migrations/" + name for name in migrations] + ["db/migrations/" + SUCCESSOR_249]
    excluded += ["db/migrations/" + name for name in later]
    member = "source='db/migrations' AND filename IN (" + ",".join(map(transition.literal, excluded)) + ")"
    digest = psql(
        "BEGIN READ ONLY; SET LOCAL statement_timeout='30s'; "
        + transition.ledger_digest_sql("NOT (" + member + ")")
        + "; COMMIT;",
        environment,
    )
    require(digest == contract["predecessor_ledger_sha256"], "post-C0 predecessor ledger drift")
    # Later migrations may intentionally change ordinary runtime grants. The
    # exact 249 boundary is checked before the first one. Every reviewed
    # receipt successor advances both runtime digests with an exact source pin.
    sql = """BEGIN READ ONLY;
SET LOCAL statement_timeout='30s';
SELECT jsonb_build_object(
    'api', encode(public.fn_runtime_ordinary_boundary_digest('verdify_api_runtime_login'), 'hex'),
    'ingestor', encode(public.fn_runtime_ordinary_boundary_digest('verdify_ingestor_runtime_login'), 'hex'),
    'api_receipt', (SELECT encode(boundary_sha256, 'hex') FROM public.runtime_ordinary_login_attestation_receipts
                    WHERE login_name='verdify_api_runtime_login'),
    'ingestor_receipt', (SELECT encode(boundary_sha256, 'hex') FROM public.runtime_ordinary_login_attestation_receipts
                         WHERE login_name='verdify_ingestor_runtime_login'),
    'receipt_count', (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts),
    'column_update', has_column_privilege('verdify_ingestor_runtime_login', 'public.daily_summary',
                                          'climate_observed_minute_metrics', 'UPDATE'),
    'table_update', has_table_privilege('verdify_ingestor_runtime_login', 'public.daily_summary', 'UPDATE'));
COMMIT;"""
    state = json.loads(psql(sql, environment))
    expected = {
        "api": SUCCESSOR_249_DIGESTS["verdify_api_runtime_login"],
        "ingestor": SUCCESSOR_249_DIGESTS["verdify_ingestor_runtime_login"],
        "api_receipt": SUCCESSOR_249_DIGESTS["verdify_api_runtime_login"],
        "ingestor_receipt": SUCCESSOR_249_DIGESTS["verdify_ingestor_runtime_login"],
        "receipt_count": 2,
        "column_update": True,
        "table_update": False,
    }
    for name, digests in (
        (SUCCESSOR_254, SUCCESSOR_254_DIGESTS),
        (SUCCESSOR_255, SUCCESSOR_255_DIGESTS),
        (SUCCESSOR_256, SUCCESSOR_256_DIGESTS),
        (SUCCESSOR_257, SUCCESSOR_257_DIGESTS),
        (SUCCESSOR_258, SUCCESSOR_258_DIGESTS),
        (SUCCESSOR_259, SUCCESSOR_259_DIGESTS),
        (SUCCESSOR_260, SUCCESSOR_260_DIGESTS),
        (SUCCESSOR_261, SUCCESSOR_261_DIGESTS),
        (SUCCESSOR_262, SUCCESSOR_262_DIGESTS),
        (SUCCESSOR_263, SUCCESSOR_263_DIGESTS),
    ):
        if name in later:
            expected["api"] = expected["api_receipt"] = digests["verdify_api_runtime_login"]
            expected["ingestor"] = expected["ingestor_receipt"] = digests["verdify_ingestor_runtime_login"]
    if later and SUCCESSOR_254 not in later:
        state.pop("api", None)
        state.pop("ingestor", None)
        expected.pop("api")
        expected.pop("ingestor")
    require(state == expected, "reviewed post-249 boundary is not exact")
    if SUCCESSOR_259 in later:
        mcp_sql = """BEGIN READ ONLY;
SET LOCAL statement_timeout='30s';
SELECT jsonb_build_object(
    'mcp', encode(public.fn_mcp_runtime_boundary_digest(), 'hex'),
    'mcp_receipt', (SELECT encode(boundary_sha256, 'hex')
                      FROM public.mcp_runtime_boundary_receipt WHERE singleton),
    'mcp_login', (SELECT rolcanlogin AND rolinherit AND NOT rolsuper
                    FROM pg_roles WHERE rolname='verdify_mcp_runtime_login'),
    'mcp_arm_read', has_table_privilege('verdify_mcp_runtime_login',
                        'public.control_assignments', 'SELECT'),
    'mcp_experiment_read', has_table_privilege('verdify_mcp_runtime_login',
                               'public.control_experiments', 'SELECT'),
    'outcome_scorecard', to_regprocedure('public.fn_climate_action_daily_scorecard(date)') IS NOT NULL,
    'outcome_scorecard_exec', coalesce(has_function_privilege(
         'verdify_mcp_runtime_login',
         to_regprocedure('public.fn_climate_action_daily_scorecard(date)'), 'EXECUTE'), false),
    'outcome_band_exec', has_function_privilege('verdify_mcp_runtime_login',
         'public.fn_crop_band_value(text,text,timestamptz,text,text,text)', 'EXECUTE'),
    'outcome_season_exec', has_function_privilege('verdify_mcp_runtime_login',
         'public.fn_current_season()', 'EXECUTE'),
    'outcome_zone_exec', has_function_privilege('verdify_mcp_runtime_login',
         'public.fn_zone_vpd_targets(timestamptz)', 'EXECUTE'),
    'outcome_anchor_read', has_table_privilege('verdify_mcp_runtime_login',
         'public.crop_band_anchors', 'SELECT'),
    'outcome_profile_read', has_table_privilege('verdify_mcp_runtime_login',
         'public.crop_target_profiles', 'SELECT'));
COMMIT;"""
        mcp_state = json.loads(psql(mcp_sql, environment))
        repaired = SUCCESSOR_260 in later
        acl_repaired = SUCCESSOR_261 in later
        native_ledger = SUCCESSOR_262 in later
        fixed_grid = SUCCESSOR_263 in later
        require(
            mcp_state
            == {
                "mcp": SUCCESSOR_263_MCP_DIGEST
                if fixed_grid
                else SUCCESSOR_262_MCP_DIGEST
                if native_ledger
                else SUCCESSOR_261_MCP_DIGEST
                if acl_repaired
                else (SUCCESSOR_260_MCP_DIGEST if repaired else SUCCESSOR_259_MCP_DIGEST),
                "mcp_receipt": SUCCESSOR_263_MCP_DIGEST
                if fixed_grid
                else SUCCESSOR_262_MCP_DIGEST
                if native_ledger
                else SUCCESSOR_261_MCP_DIGEST
                if acl_repaired
                else (SUCCESSOR_260_MCP_DIGEST if repaired else SUCCESSOR_259_MCP_DIGEST),
                "mcp_login": True,
                "mcp_arm_read": False,
                "mcp_experiment_read": False,
                "outcome_scorecard": repaired,
                "outcome_scorecard_exec": repaired,
                "outcome_band_exec": acl_repaired,
                "outcome_season_exec": acl_repaired,
                "outcome_zone_exec": acl_repaired,
                "outcome_anchor_read": acl_repaired,
                "outcome_profile_read": acl_repaired,
            },
            "reviewed MCP ordinary boundary is not exact",
        )


def run_successor_249(directory, environment, *, plan):
    runner = ROOT / "db/apply-migrations.sh"
    if not runner.is_file():
        runner = Path("/usr/local/bin/apply-migrations.sh")
    require(runner.is_file(), "ledgered successor runner missing")
    with tempfile.TemporaryDirectory(prefix="verdify-c0-successor-") as temporary:
        shutil.copyfile(directory / SUCCESSOR_249, Path(temporary) / SUCCESSOR_249)
        env = dict(environment, VERDIFY_MIGRATIONS_DIR=temporary)
        command = ["sh", str(runner)] + (["--plan"] if plan else [])
        result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=1200)
        require(result.returncode == 0, "ledgered successor runner failed")


def run_post_249(directory, later, environment, *, plan):
    if not later:
        return
    runner = ROOT / "db/apply-migrations.sh"
    if not runner.is_file():
        runner = Path("/usr/local/bin/apply-migrations.sh")
    require(runner.is_file(), "ledgered post-249 runner missing")
    with tempfile.TemporaryDirectory(prefix="verdify-post-249-") as temporary:
        for name in sorted(later):
            shutil.copyfile(directory / name, Path(temporary) / name)
        env = dict(environment, VERDIFY_MIGRATIONS_DIR=temporary)
        command = ["sh", str(runner)] + (["--plan"] if plan else [])
        result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=3600)
        require(result.returncode == 0, "ledgered post-249 runner failed")


def deliver_resource_successor(directory, files, rows, contract, environment, *, plan):
    later = post_249_inventory(files, rows)
    reviewed_post_254(later, files)
    pending_later = [name for name in later if ("db/migrations", "db/migrations/" + name) not in rows]
    row = rows.get(("db/migrations", "db/migrations/" + SUCCESSOR_249))
    if row is not None:
        applied_later = [name for name in later if rows.get(("db/migrations", "db/migrations/" + name))]
        verify_post_249(contract, environment, later=applied_later)
        print("Post-C0 migration 249 verified.")
    else:
        psql(successor_probe(contract), environment)
        run_successor_249(directory, environment, plan=plan)
        if not plan:
            after_rows = ledger_rows(environment)
            require(completed_resource_successor(files, after_rows, contract), "successor ledger readback failed")
            require(
                after_rows.get(("db/migrations", "db/migrations/" + SUCCESSOR_249)) is not None,
                "successor stamp missing after transaction",
            )
            verify_post_249(contract, environment)
            print("Post-C0 migration 249 committed with exact grant, receipts and ledger stamp.")
        else:
            print("C0 successor PLAN: reviewed migration 249; no writes.")
    if later:
        if pending_later or plan:
            run_post_249(directory, later, environment, plan=plan)
        if not plan:
            after_rows = ledger_rows(environment)
            require(len(post_249_inventory(files, after_rows)) == len(later), "post-249 inventory readback failed")
            require(
                all(after_rows.get(("db/migrations", "db/migrations/" + name)) for name in later),
                "post-249 stamp missing after runner",
            )
            verify_post_249(contract, environment, later=list(later))
            print(f"Post-C0 migrations 250+ verified: {len(later)} exact runner stamps.")
        else:
            print(f"Post-C0 successor PLAN: {len(later)} later migration(s); no writes.")


def deliver(directory, *, plan=False, environment=None):
    env = dict(os.environ if environment is None else environment)
    files = inventory(directory)
    contract, pin = load_contract(env, plan=plan)
    version = contract["version"] if contract else transition.VERSION
    migrations = transition.release_migrations(version)
    require(
        all(files.get(name) == sha for name, sha in migrations.items()),
        "complete exact selected release inventory required",
    )
    transition.checked_sources(version)
    # Preserve the entrypoint's existing core/Timescale guard after dispatch,
    # but never reach schema replay or repair when this prerequisite is missing.
    core = json.loads(psql(CORE_SQL, env))
    require(
        isinstance(core, dict) and core.get("timescale") is True and core.get("core") is True,
        "Timescale extension and existing core schema required",
    )
    if version == transition.RESOURCE_VERSION:
        require(core.get("timescale_version") == "2.25.2", "resource qualification requires TimescaleDB 2.25.2")
    rows = ledger_rows(env)
    if completed_resource_successor(files, rows, contract):
        deliver_resource_successor(directory, files, rows, contract, env, plan=plan)
        return
    pending = verify_inventory_ledger(files, rows, version=version)
    if plan:
        bundle = "240-248" if version == transition.RESOURCE_VERSION else "241-247"
        print(f"C0 PLAN: {pending} pending; atomic bundle={bundle}; contract_supplied={contract is not None}.")
        print("Read-only inventory check only; target fingerprints, execution and deployment remain unverified.")
        return
    sql = transition.emit_sql(contract, pin)
    sql_sha = hashlib.sha256(sql.encode()).hexdigest()
    print(f"C0 atomic delivery: contract_sha256={pin}; sql_sha256={sql_sha}", flush=True)
    psql(sql, env)
    verify_inventory_ledger(files, ledger_rows(env), after=True, version=version)
    psql(successor_probe(contract), env)
    count = "nine" if version == transition.RESOURCE_VERSION else "seven"
    print(f"C0 committed state verified: {count} exact stamps and both successor receipts/catalogs.")
    print("Ordinary application sessions, Argo health and live consumer adoption require separate verification.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrations-dir", type=Path, required=True)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args(argv)
    try:
        deliver(args.migrations_dir, plan=args.plan)
    except (OSError, ValueError, TypeError, OverflowError, subprocess.TimeoutExpired) as exc:
        # Only locally authored diagnostics may be surfaced. Never echo paths,
        # subprocess arguments, contract values or raw DB errors from exceptions.
        suffix = " " + str(exc) + "." if isinstance(exc, DeliveryError) else ""
        print("C0 delivery refused or unverified; no per-file fallback." + suffix, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
