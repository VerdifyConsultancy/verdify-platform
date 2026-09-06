"""Emit a reviewed, exact-fingerprint C0 transaction; never connect to a DB.

No production contract ships with this tool. A contract hash binds reviewed
bytes, not the identity/authority of their reviewer. The owning delivery source
must supply that trust and a target-specific restored qualification receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ordinary_boundary_diff", ROOT / "scripts/ordinary-boundary-diff.py")
boundary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boundary)
VERSION = "c0-boundary-transition-241-247-v1"
RESOURCE_VERSION = "c0-resource-boundary-transition-241-248-v1"
# Immutable release membership: neither filenames nor executable SQL come from
# the external contract. Any extension requires a separate source review.
MIGRATIONS = {
    "241-scorecard-binary-semantics.sql": "501590af14b957658e3da8b11abd79023812daee296f2f7ee910fc20370142f0",
    "242-outdoor-forecast-verification.sql": "80de73d174fc69c1921a362392147e271142d6357481172c07b9b3a6dde226fc",
    "243-public-band-lineage.sql": "c7d747ab72b451cdfd1ad51502e149f80e97e8b0ab9902391db5146847d62620",
    "244-daily-climate-metric-revisions.sql": "8b04febbd7c9b620c922a7e4b5be0061f74423f6eb7f9f2a77f9f1caabe914ad",
    "245-observed-minute-diagnostics.sql": "28f490631031e5e2424ff2425f8d344835d15d4b1bca79fc78c8c3559ce84e45",
    "246-observed-minute-reader.sql": "9e45f1b075ec1670392a6075f83fee4beb18d75d032adf722ebf6969312873db",
    "247-inline-climate-capture-payload.sql": "0efec8266296d930dcbef5662c13bea18256b7964db70cf412bc68630bbd0049",
}
RESOURCE_MIGRATION = {
    "248-shelly-source-interval-accounting.sql": "45b3fb28c8e11608e14407f5b18bc15018dff54c7dc8dd7b882352d961027b56",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def is_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def release_migrations(version):
    # The contract selects a separately source-reviewed fixed profile, never
    # filenames, executable SQL or hashes. The original version stays seven.
    require(version in (VERSION, RESOURCE_VERSION), "unsupported contract version")
    return MIGRATIONS if version == VERSION else {**MIGRATIONS, **RESOURCE_MIGRATION}


def validate(contract):
    require(isinstance(contract, dict), "contract must be an object")
    require(
        set(contract) == {"version", "database", "server_version_num", "predecessor_ledger_sha256", "before", "after"},
        "unexpected contract fields",
    )
    release_migrations(contract["version"])
    require(
        isinstance(contract["database"], str) and re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9-]{0,62}", contract["database"]),
        "unsupported database name",
    )
    require(
        type(contract["server_version_num"]) is int and 160000 <= contract["server_version_num"] < 170000,
        "qualification requires exact PostgreSQL 16 version",
    )
    require(is_hash(contract["predecessor_ledger_sha256"]), "invalid ledger digest")
    for stage in ("before", "after"):
        require(
            isinstance(contract[stage], dict) and set(contract[stage]) == set(boundary.LOGINS), "both logins required"
        )
        require(all(is_hash(value) for value in contract[stage].values()), "invalid boundary digest")
    require(
        all(contract["before"][login] != contract["after"][login] for login in boundary.LOGINS), "unchanged boundary"
    )


def ledger_digest_sql(where="true"):
    # Complete ledger identity, across all streams, plus recorded apply method.
    # Capture times/durations are operational metadata, not migration identity.
    return f"""SELECT encode(sha256(convert_to(coalesce(jsonb_agg(
        jsonb_build_array(source, filename, seq, sha256, stamp_method)
        ORDER BY source, filename)::text, '[]'), 'UTF8')), 'hex')
        FROM public.schema_migrations WHERE {where}"""


def checked_sources(version=VERSION):
    result = []
    for name, expected in release_migrations(version).items():
        raw = (ROOT / "db/migrations" / name).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == expected, "migration source drift")
        result.append((name, expected, raw.decode()))
    # Also verifies the immutable 217 source before extracting either function.
    boundary.source_projection(boundary.LOGINS[0])
    return result


def function_guard(version=VERSION):
    raw = boundary.SOURCE.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == boundary.SOURCE_SHA256, "attestation source drift")
    source = raw.decode()
    statements = []
    for name, signature in (
        ("fn_runtime_ordinary_boundary_digest", "text"),
        ("fn_runtime_attest_ordinary_login", ""),
    ):
        body = (
            source.split(f"CREATE OR REPLACE FUNCTION public.{name}(", 1)[1]
            .split("AS $body$", 1)[1]
            .split("$body$;", 1)[0]
        )
        expected = hashlib.sha256(body.encode()).hexdigest()
        statements.append(f"""IF NOT coalesce((SELECT
            encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') = '{expected}'
            AND p.prosecdef AND l.lanname = 'plpgsql'
            AND p.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
            AND p.proowner = (SELECT datdba FROM pg_database WHERE datname=current_database())
            FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
            WHERE p.oid=to_regprocedure('public.{name}({signature})')), false) THEN
            RAISE EXCEPTION 'C0 transition refuses changed attestation implementation';
        END IF;""")
    # Verify the installed C primitive before any call through the definer.
    # Independently hashing the projection below also detects false returns.
    statements.append("""IF NOT coalesce((SELECT
        p.prosrc = 'pg_digest' AND p.probin = '$libdir/pgcrypto'
        AND l.lanname = 'c' AND NOT p.prosecdef AND p.proisstrict AND p.provolatile = 'i'
        AND p.proconfig IS NULL AND p.prorettype = 'bytea'::regtype
        AND e.extname = 'pgcrypto'
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
        JOIN pg_depend d ON d.classid='pg_proc'::regclass AND d.objid=p.oid
            AND d.refclassid='pg_extension'::regclass AND d.deptype='e'
        JOIN pg_extension e ON e.oid=d.refobjid
        WHERE p.oid=to_regprocedure('public.digest(text,text)')), false) THEN
        RAISE EXCEPTION 'C0 transition refuses changed digest primitive';
    END IF;""")
    if version == RESOURCE_VERSION:
        statements.append("""IF (SELECT extversion FROM pg_extension WHERE extname='timescaledb')
            IS DISTINCT FROM '2.25.2' THEN
            RAISE EXCEPTION 'C0 resource transition refuses unsupported TimescaleDB version';
        END IF;""")
    return "\n".join(statements)


def ledger_shape_guard():
    # The migration ledger is not necessarily in 217's protected closure.
    # Do not execute untracked trigger/rule/check/index/default code while
    # stamping. All column values (including applied_at) are supplied explicitly.
    return """IF NOT coalesce((SELECT c.relkind='r' AND c.relpersistence='p'
        AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity AND NOT c.relispartition
        AND c.relowner=(SELECT datdba FROM pg_database WHERE datname=current_database())
        FROM pg_class c WHERE c.oid='public.schema_migrations'::regclass), false)
        OR EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='public.schema_migrations'::regclass)
        OR EXISTS (SELECT 1 FROM pg_rewrite WHERE ev_class='public.schema_migrations'::regclass)
        OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid='public.schema_migrations'::regclass)
        OR EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.schema_migrations'::regclass
            AND attnum>0 AND (attisdropped OR attgenerated<>'' OR attidentity<>''))
        OR EXISTS (SELECT 1 FROM pg_class c,
            LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
            WHERE c.oid='public.schema_migrations'::regclass AND a.grantee<>c.relowner
              AND a.privilege_type<>'SELECT')
        OR EXISTS (SELECT 1 FROM pg_attribute col JOIN pg_class c ON c.oid=col.attrelid,
            LATERAL aclexplode(col.attacl) a
            WHERE c.oid='public.schema_migrations'::regclass AND a.grantee<>c.relowner
              AND a.privilege_type<>'SELECT')
        OR EXISTS (SELECT 1 FROM pg_inherits WHERE inhrelid='public.schema_migrations'::regclass
            OR inhparent='public.schema_migrations'::regclass)
        OR (SELECT count(*) FROM pg_index WHERE indrelid='public.schema_migrations'::regclass) <> 1
        OR (SELECT count(*) FROM pg_constraint WHERE conrelid='public.schema_migrations'::regclass) <> 1
        OR NOT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_index i ON i.indexrelid=c.conindid
            WHERE c.conrelid='public.schema_migrations'::regclass AND c.contype='p'
            AND NOT c.condeferrable AND c.convalidated AND i.indisvalid AND i.indisready
            AND i.indexprs IS NULL AND i.indpred IS NULL
            AND pg_get_constraintdef(c.oid)='PRIMARY KEY (source, filename)')
        OR (SELECT array_agg(attname || ':' || format_type(atttypid,atttypmod) || ':' || attnotnull::text
                ORDER BY attnum) FROM pg_attribute
            WHERE attrelid='public.schema_migrations'::regclass AND attnum>0 AND NOT attisdropped)
            IS DISTINCT FROM ARRAY['filename:text:true','source:text:true','seq:integer:false',
                'sha256:text:false','applied_at:timestamp with time zone:true','stamp_method:text:true',
                'duration_ms:integer:false','applied_by:text:false']::text[] THEN
        RAISE EXCEPTION 'C0 transition refuses unexpected ledger shape or executable hooks';
    END IF;"""


def digest_guard(expected, receipt_expected=None):
    statements = []
    for login in boundary.LOGINS:
        _, cte = boundary.source_projection(login)
        statements.append(f"""{cte}
        SELECT encode(sha256(convert_to(coalesce(string_agg(entry, E'\\n' ORDER BY entry), ''), 'UTF8')), 'hex')
          INTO v_digest FROM security_entries;
        IF v_digest IS DISTINCT FROM '{expected[login]}'
           OR encode(public.fn_runtime_ordinary_boundary_digest('{login}'), 'hex')
              IS DISTINCT FROM v_digest THEN
            RAISE EXCEPTION 'C0 transition refuses unreviewed catalog boundary';
        END IF;""")
        if receipt_expected is not None:
            statements.append(f"""IF (SELECT encode(boundary_sha256, 'hex')
                FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='{login}')
                IS DISTINCT FROM '{receipt_expected[login]}' THEN
                RAISE EXCEPTION 'C0 transition refuses stale or missing receipt';
            END IF;""")
    return "\n".join(statements)


def emit_sql(contract, contract_sha256):
    validate(contract)
    require(is_hash(contract_sha256), "invalid contract binding")
    version = contract["version"]
    migrations = release_migrations(version)
    sources = checked_sources(version)
    paths = ["db/migrations/" + name for name in migrations]
    member = "source='db/migrations' AND filename IN (" + ",".join(map(literal, paths)) + ")"
    values = ",\n".join(
        f"({literal('db/migrations/' + name)}, '{sha}', {int(name[:3])})" for name, sha in migrations.items()
    )
    identity = function_guard(version)
    before = digest_guard(contract["before"], contract["before"])
    after = digest_guard(contract["after"], contract["after"])
    successor = digest_guard(contract["after"], contract["before"])
    sql = f"""-- {version}; reviewed contract SHA256 {contract_sha256}
-- This is mutating SQL. Execute only via qualified owning delivery, psql -X.
\\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path = pg_catalog, public, pg_temp;
SET LOCAL statement_timeout = '15min';
SET LOCAL lock_timeout = '30s';
SET LOCAL idle_in_transaction_session_timeout = '5min';
SELECT pg_advisory_xact_lock(hashtext('verdify-schema-migrations'));
LOCK TABLE public.schema_migrations, public.runtime_ordinary_login_attestation_receipts IN ACCESS EXCLUSIVE MODE;
-- All deparsed catalog fingerprints use the exact 217 definer context.
SET LOCAL search_path = pg_catalog, pg_temp;
DO $c0_preflight$
DECLARE v_count integer; v_digest text;
BEGIN
    IF current_database() <> {literal(contract["database"])}
       OR current_setting('server_version_num')::integer <> {contract["server_version_num"]}
       OR current_user <> session_user
       OR current_user <> (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()) THEN
        RAISE EXCEPTION 'C0 transition refuses target, server or migration identity mismatch';
    END IF;
    {identity}
    {ledger_shape_guard()}
    IF ({ledger_digest_sql("NOT (" + member + ")")}) IS DISTINCT FROM '{contract["predecessor_ledger_sha256"]}' THEN
        RAISE EXCEPTION 'C0 transition refuses predecessor ledger drift';
    END IF;
    IF (SELECT count(*) FROM public.runtime_ordinary_login_attestation_receipts) <> 2 THEN
        RAISE EXCEPTION 'C0 transition requires exactly two receipts';
    END IF;
    SELECT count(*) INTO v_count FROM public.schema_migrations WHERE {member};
    IF v_count = 0 THEN
        {before}
    ELSIF v_count = {len(migrations)} AND NOT EXISTS (
        SELECT 1 FROM (VALUES {values}) expected(filename, sha, seq)
        LEFT JOIN public.schema_migrations actual ON actual.source='db/migrations' AND actual.filename=expected.filename
        WHERE actual.sha256 IS DISTINCT FROM expected.sha OR actual.seq IS DISTINCT FROM expected.seq
           OR actual.stamp_method IS DISTINCT FROM 'runner') THEN
        {after}
    ELSE
        RAISE EXCEPTION 'C0 transition refuses partial or changed release ledger';
    END IF;
END;
$c0_preflight$;
SELECT EXISTS(SELECT 1 FROM public.schema_migrations WHERE {member}) AS c0_already_applied \\gset
\\if :c0_already_applied
\\echo Exact successor already applied; no writes.
\\else
SET LOCAL search_path = pg_catalog, public, pg_temp;
"""
    for name, sha, source in sources:
        sql += f"""\n-- BEGIN EXACT SOURCE {name} SHA256 {sha}
SELECT clock_timestamp() AS c0_started \\gset
{source}
-- END EXACT SOURCE {name}
INSERT INTO public.schema_migrations
    (filename, source, seq, sha256, stamp_method, applied_at, duration_ms, applied_by)
VALUES ('db/migrations/{name}', 'db/migrations', {int(name[:3])}, '{sha}', 'runner', clock_timestamp(),
    (extract(epoch FROM clock_timestamp() - :'c0_started'::timestamptz) * 1000)::integer, current_user);
"""
    sql += f"""
SET LOCAL search_path = pg_catalog, pg_temp;
DO $c0_successor$
DECLARE v_digest text; v_rows integer;
BEGIN
    {identity}
    {ledger_shape_guard()}
    {successor}
    -- Only reviewed literals are written, never a freshly computed digest.
    UPDATE public.runtime_ordinary_login_attestation_receipts
       SET boundary_sha256 = CASE login_name
           WHEN '{boundary.LOGINS[0]}' THEN decode('{contract["after"][boundary.LOGINS[0]]}', 'hex')
           WHEN '{boundary.LOGINS[1]}' THEN decode('{contract["after"][boundary.LOGINS[1]]}', 'hex') END,
           captured_at = clock_timestamp()
       WHERE login_name IN ('{boundary.LOGINS[0]}', '{boundary.LOGINS[1]}');
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows <> 2 THEN RAISE EXCEPTION 'C0 transition receipt write count mismatch'; END IF;
    {after}
END;
$c0_successor$;
\\endif
COMMIT;
\\echo C0 exact-boundary transaction committed; verify ordinary sessions and live consumers separately.
"""
    return sql


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument(
        "--contract-sha256", required=True, help="hash pinned independently in reviewed owning delivery source"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        contract, actual_sha = boundary.read_snapshot(args.contract)
        require(actual_sha == args.contract_sha256, "contract bytes do not match reviewed hash")
        sql = emit_sql(contract, actual_sha)
        with args.output.open("x") as stream:
            stream.write(sql)
        print(f"Emitted transaction sha256={hashlib.sha256(sql.encode()).hexdigest()}; no database contacted.")
    except (OSError, ValueError, TypeError, OverflowError):
        print(
            "C0 transition refused: invalid contract/source or unavailable output; no input values disclosed.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
