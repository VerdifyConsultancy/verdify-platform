"""Closed post269 -> 270 logical-target successor SQL; no native execution.

Emits a genuine rollback qualification, or a reviewed installation, for the
already admitted isolated target. This is source-identical ops DDL with a
qualified-target pre/postflight, not execution of the production migration.
Existing logical/physical admission and production migration bytes are untouched.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("successor_retained", ROOT / "scripts/cnpg-retained-session-admission.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
t = r.t
VERSION = "cnpg-logical-target270-successor-v1"
MIGRATION = "db/migrations/270-facility-safe-ops-projection.sql"
MIGRATION_SHA = "672d1afa92e37f243d5893fbd57cd2b84e86ea19c1c79d3975c04dfd39663532"
OLD_BODY_SHA = "13213dadb5a6c114757787baaa2d84981b74baa1fc9fb8968bfe9f96f911e06c"
NEW_BODY_SHA = "1bb0e81cda13b7cade16104fe9456a5c9dbeb45fc0398a81e767f308c75ce73f"
IDENTITY = "fn_experiment_v2_ops_status()"
CATALOG_ID = "public." + IDENTITY
ENTRY_PREFIX = "function|" + CATALOG_ID + "|"
LEDGER_ROW = ["db/migrations", MIGRATION, 270, MIGRATION_SHA, "runner"]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def selected_source():
    raw = (ROOT / MIGRATION).read_bytes()
    t.c0.require(digest(raw) == MIGRATION_SHA, "applied 270 bytes changed")
    t.classify_ddl(raw.decode())
    marker = "CREATE OR REPLACE FUNCTION public." + IDENTITY
    text = raw.decode()
    t.c0.require(text.count(marker) == 1, "closed ops DDL required")
    ddl = marker + text.split(marker, 1)[1].split("$body$;", 1)[0] + "$body$;"
    body = ddl.split("AS $body$", 1)[1].split("$body$;", 1)[0]
    old_raw = (ROOT / "db/migrations/217-runtime-role-boundary.sql").read_bytes()
    t.c0.require(digest(old_raw) == t.c0.boundary.SOURCE_SHA256, "ops predecessor source changed")
    old = old_raw.decode().split(marker, 1)[1].split("AS $body$", 1)[1].split("$body$;", 1)[0]
    t.c0.require(
        digest(old.encode()) == OLD_BODY_SHA and digest(body.encode()) == NEW_BODY_SHA, "ops body binding changed"
    )
    t.classify_ddl(ddl)
    return ddl, old, body


def successor_entries(entries, old, new):
    """Only the one identified function's definition and prosrc may change."""
    selected = [entry for entry in entries if entry.startswith(ENTRY_PREFIX)]
    t.c0.require(len(selected) == 1 and selected[0].count(old) == 2, "exact ops definition/body entry required")
    return sorted(entry.replace(old, new) if entry.startswith(ENTRY_PREFIX) else entry for entry in entries)


def catalog_without_ops(entries):
    matches = [entry for entry in entries if entry[:2] == ["function", CATALOG_ID]]
    t.c0.require(len(matches) == 1, "unique full ops catalog identity required")
    return [entry for entry in entries if entry[:2] != ["function", CATALOG_ID]]


def validate_post(before, after):
    """Independent full-record validation. SQL additionally binds catalog hashes
    to the actual unchanged pg_proc metadata and exact source definition."""
    _, old, new = selected_source()
    t.c0.checked(before, target=True)
    t.c0.checked(after, target=True)
    t.c0.require(set(before) == set(after), "successor witness shape changed")
    changing = {"ledger", "portable_catalog", "raw_portable_catalog_v2", "boundaries"}
    for key in before.keys() - changing:
        t.c0.require(before[key] == after[key], "unexpected successor fact: " + key)
    t.c0.require(
        not any(row[0] == "db/migrations" and row[2] >= 270 for row in before["ledger"]),
        "270 predecessor already advanced",
    )
    t.c0.require(
        after["ledger"] == sorted(before["ledger"] + [LEDGER_ROW], key=lambda x: (x[0], x[1])),
        "closed 270 ledger delta required",
    )
    for field in ("portable_catalog", "raw_portable_catalog_v2"):
        t.c0.require(
            catalog_without_ops(before[field]) == catalog_without_ops(after[field]), "unexpected full catalog delta"
        )
    t.c0.require(set(before["boundaries"]) == set(after["boundaries"]) == set(t.LOGINS), "closed boundary set required")
    for login in t.LOGINS:
        a, b = before["boundaries"][login], after["boundaries"][login]
        if login == "verdify_mcp_runtime_login":
            t.c0.require(a == b, "MCP boundary changed")
            continue
        t.c0.require(set(a) == set(b), "ordinary witness shape changed")
        for field in a.keys() - {"raw_entries", "semantic_entries", "native"}:
            t.c0.require(a[field] == b[field], "attester implementation changed")
        for field in ("raw_entries", "semantic_entries"):
            t.c0.require(b[field] == successor_entries(a[field], old, new), "unexpected ordinary boundary delta")


def validate_ops(before, after, witness_before, witness_after):
    _, old, new = selected_source()
    t.c0.require(
        set(before) == set(after) == {"native", "definition", "catalog_definition_text"}, "closed ops facts required"
    )
    a, b = before["native"], after["native"]
    t.c0.require(a["prosrc"] == old and b["prosrc"] == new, "ops source body changed")
    t.c0.require(
        {k: v for k, v in a.items() if k != "prosrc"} == {k: v for k, v in b.items() if k != "prosrc"},
        "ops metadata changed",
    )
    t.c0.require(
        before["definition"].count(old) == 1 and after["definition"] == before["definition"].replace(old, new),
        "ops native definition changed",
    )
    payloads = []
    for fact, witness in [(before, witness_before), (after, witness_after)]:
        text = fact["catalog_definition_text"]
        payload = json.loads(text, object_pairs_hook=t.c0.boundary._pairs)
        t.c0.require(
            len(payload) == 3
            and payload[0] == witness["roles"][str(fact["native"]["proowner"])]
            and payload[1] == fact["definition"],
            "ops catalog payload binding mismatch",
        )
        t.c0.require(
            text == json.dumps(payload, ensure_ascii=False), "ops native JSON definition serialization changed"
        )
        for field in ("portable_catalog", "raw_portable_catalog_v2"):
            rows = [row for row in witness[field] if row[:2] == ["function", CATALOG_ID]]
            t.c0.require(
                rows == [["function", CATALOG_ID, digest(text.encode())]], "ops catalog definition hash changed"
            )
        payloads.append(payload)
    t.c0.require(
        payloads[0][0] == payloads[1][0] and payloads[0][2] == payloads[1][2], "ops owner or effective ACL changed"
    )


def checked_record(before, record, *, mode="rollback-qualification"):
    ddl, _, _ = selected_source()
    t.c0.require(
        set(record) == {"version", "mode", "ddl_sha256", "before_witness", "post_witness", "ops_before", "ops_after"},
        "closed successor record required",
    )
    t.c0.require(
        mode in {"rollback-qualification", "install"} and record["version"] == VERSION and record["mode"] == mode,
        "genuine successor record mode required",
    )
    t.c0.require(
        record["ddl_sha256"] == digest(ddl.encode()) and record["before_witness"] == before,
        "source/predecessor mismatch",
    )
    validate_post(before, record["post_witness"])
    validate_ops(record["ops_before"], record["ops_after"], before, record["post_witness"])
    return record["post_witness"]


def receipts_sql():
    return f"(SELECT jsonb_agg(to_jsonb(x) ORDER BY login_name) FROM {t.TABLE} x)"


def proc_sql():
    return "(SELECT to_jsonb(p)-'xmin' FROM pg_proc p WHERE p.oid='public.fn_experiment_v2_ops_status()'::regprocedure)"


def ops_sql():
    payload = f"jsonb_build_array(pg_get_userbyid(p.proowner),pg_get_functiondef(p.oid),{t.c0.acl_sql('p.proacl')})"
    return f"(SELECT jsonb_build_object('native',to_jsonb(p),'definition',pg_get_functiondef(p.oid),'catalog_definition_text',{payload}::text) FROM pg_proc p WHERE p.oid='public.fn_experiment_v2_ops_status()'::regprocedure)"


def emit_sql(before, *, prior_rows, reviewed=None, qualification_sha=None):
    """Complete standalone transaction, rollback by default. Caller uses existing
    peer bootstrap wrapper and exact UID/image/source custody transport."""
    t.c0.checked(before, target=True)
    validate_prior_rows(prior_rows)
    ddl, old, new = selected_source()
    post = checked_record(before, reviewed) if reviewed is not None else None
    t.c0.require((post is None) == (qualification_sha is None), "complete reviewed qualification required")
    if post is not None:
        t.c0.require(t.transaction.is_hash(qualification_sha), "actual qualification hash required")
    witness = t.witness_select(before)
    split = witness.index("SELECT jsonb_build_object(")
    guards = witness[:split]
    select = witness[split:].rstrip().removesuffix(";")
    lit = t.literal
    new_body = lit(new)
    old_body = lit(old)
    install = ""
    if post is not None:
        values = ",".join(
            f"({lit(login)},decode({lit(post['boundaries'][login]['native'])},'hex'))" for login in t.LOGINS
        )
        install = f"""UPDATE {t.TABLE} receipt SET boundary_sha256=value.boundary,
 qualification_sha256={lit(qualification_sha)} FROM (VALUES {values}) value(login,boundary)
 WHERE receipt.login_name=value.login;
 IF (SELECT count(*) FROM {t.TABLE})<>3 OR EXISTS(SELECT 1 FROM {t.TABLE} receipt
 WHERE receipt.qualification_sha256<>{lit(qualification_sha)}) THEN
 RAISE EXCEPTION 'successor receipt update refused'; END IF;"""
    # Every complete input remains outside the PL/pgSQL compile body, with an
    # immutable DO-entry capture. Full witness and raw custody are never shrunk.
    sql = f"""\\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path=pg_catalog,pg_temp;
SET LOCAL statement_timeout='180s';
SET LOCAL lock_timeout='2s';
{r.identity_sql()}
SELECT pg_advisory_xact_lock(hashtext('verdify-schema-migrations'));
LOCK TABLE public.schema_migrations,{t.TABLE} IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE public.runtime_ordinary_login_attestation_receipts,public.mcp_runtime_boundary_receipt IN SHARE MODE;
{t.refresh_custody_sql()}
{guards}
{t.transaction_witness_inputs_sql(before, post)}
SELECT set_config('verdify.cnpg270_expected_rows',{lit(json.dumps(prior_rows, separators=(",", ":")))},true) IS NOT NULL;
DO $successor270$
DECLARE v_expected jsonb := current_setting('verdify.cnpg_transition_expected_before')::jsonb;
 v_reviewed jsonb := current_setting('verdify.cnpg_transition_expected_post')::jsonb;
 v_prior_rows jsonb := current_setting('verdify.cnpg270_expected_rows')::jsonb;
 v_before jsonb; v_post jsonb; v_proc jsonb; v_definition text; v_ops_before jsonb; v_ops_after jsonb;
 v_original jsonb; v_receipts jsonb; v_roles jsonb; v_members jsonb; v_started timestamptz := clock_timestamp();
 v_login text; v_field text; v_expected_entries jsonb;
BEGIN
 {select} INTO v_before;
 IF v_before IS DISTINCT FROM v_expected THEN RAISE EXCEPTION 'successor stale full predecessor'; END IF;
 IF NOT coalesce(({t.target_receipt_shape()}),false) OR
 (SELECT count(*) FROM {t.TABLE})<>3 OR
 (SELECT count(DISTINCT qualification_sha256) FROM {t.TABLE})<>1 OR
 EXISTS(SELECT 1 FROM {t.TABLE} receipt WHERE encode(boundary_sha256,'hex')
 IS DISTINCT FROM v_before->'boundaries'->receipt.login_name->>'native') THEN
 RAISE EXCEPTION 'successor target admission missing'; END IF;
 IF EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq>=270)
 OR NOT EXISTS(SELECT 1 FROM public.schema_migrations WHERE source='db/migrations' AND seq=269
 AND sha256='2540c20810b07fa42e73200d120d3b753b9b36860b029a49b8db1b4a7283475d' AND stamp_method='runner') THEN
 RAISE EXCEPTION 'successor ledger predecessor refused'; END IF;
 SELECT jsonb_agg(to_jsonb(r)-'rolpassword' ORDER BY oid) INTO v_roles FROM pg_roles r;
 SELECT jsonb_agg(to_jsonb(m) ORDER BY oid) INTO v_members FROM pg_auth_members m;
 SELECT {t.original_facts_sql()} INTO v_original;
 SELECT {receipts_sql()} INTO v_receipts;
 IF v_original IS DISTINCT FROM v_prior_rows->0 OR v_receipts IS DISTINCT FROM v_prior_rows->1 THEN
 RAISE EXCEPTION 'successor immutable historical row custody changed'; END IF;
 SELECT {ops_sql()} INTO v_ops_before;
 SELECT {proc_sql()},pg_get_functiondef('public.fn_experiment_v2_ops_status()'::regprocedure) INTO v_proc,v_definition;
 IF v_proc->>'prosrc' IS DISTINCT FROM {old_body} THEN RAISE EXCEPTION 'successor source body predecessor refused'; END IF;
 {ddl}
 IF ({proc_sql()} - 'prosrc') IS DISTINCT FROM (v_proc - 'prosrc') OR
 ({proc_sql()}->>'prosrc') IS DISTINCT FROM {new_body} OR
 pg_get_functiondef('public.fn_experiment_v2_ops_status()'::regprocedure)
 IS DISTINCT FROM replace(v_definition,{old_body},{new_body}) THEN
 RAISE EXCEPTION 'successor changed function metadata or definition'; END IF;
 -- Explicit target-qualified runner artifact, not the production pre/postflight.
 INSERT INTO public.schema_migrations(filename,source,seq,sha256,stamp_method,applied_at,duration_ms,applied_by)
 VALUES ('{MIGRATION}','db/migrations',270,'{MIGRATION_SHA}','runner',clock_timestamp(),
 (extract(epoch FROM clock_timestamp()-v_started)*1000)::integer,current_user);
 SELECT {ops_sql()} INTO v_ops_after;
 {select} INTO v_post;
 IF (v_post - ARRAY['ledger','portable_catalog','raw_portable_catalog_v2','boundaries'])
 IS DISTINCT FROM (v_before - ARRAY['ledger','portable_catalog','raw_portable_catalog_v2','boundaries']) THEN
 RAISE EXCEPTION 'successor changed raw/role/seal/native facts'; END IF;
 IF (SELECT jsonb_agg(value ORDER BY value->>0,value->>1) FROM jsonb_array_elements(v_post->'ledger')
 WHERE value IS DISTINCT FROM {lit(json.dumps(LEDGER_ROW, separators=(",", ":")))}::jsonb)
 IS DISTINCT FROM v_before->'ledger' THEN RAISE EXCEPTION 'successor changed historical ledger'; END IF;
 -- Full catalog hashes are computed from the actual independently constrained
 -- pg_proc definitions and ACLs. Only the one exact ops identity may differ.
 FOR v_field IN SELECT unnest(ARRAY['portable_catalog','raw_portable_catalog_v2']) LOOP
 IF (SELECT count(*) FROM jsonb_array_elements(v_before->v_field) WHERE value->>0='function' AND value->>1='{CATALOG_ID}')<>1
 OR (SELECT count(*) FROM jsonb_array_elements(v_post->v_field) WHERE value->>0='function' AND value->>1='{CATALOG_ID}')<>1 OR
 (SELECT jsonb_agg(value ORDER BY ord) FROM jsonb_array_elements(v_before->v_field) WITH ORDINALITY x(value,ord)
 WHERE NOT(value->>0='function' AND value->>1='{CATALOG_ID}')) IS DISTINCT FROM
 (SELECT jsonb_agg(value ORDER BY ord) FROM jsonb_array_elements(v_post->v_field) WITH ORDINALITY x(value,ord)
 WHERE NOT(value->>0='function' AND value->>1='{CATALOG_ID}')) THEN
 RAISE EXCEPTION 'successor uncontrolled catalog delta'; END IF;
 END LOOP;
 FOREACH v_login IN ARRAY ARRAY['verdify_api_runtime_login','verdify_ingestor_runtime_login'] LOOP
 IF ((v_post->'boundaries'->v_login) - ARRAY['native','raw_entries','semantic_entries']) IS DISTINCT FROM
 ((v_before->'boundaries'->v_login) - ARRAY['native','raw_entries','semantic_entries']) THEN
 RAISE EXCEPTION 'successor changed digest/attester implementation'; END IF;
 FOREACH v_field IN ARRAY ARRAY['raw_entries','semantic_entries'] LOOP
 IF (SELECT count(*) FROM jsonb_array_elements_text(v_before->'boundaries'->v_login->v_field)
 WHERE left(value,length('{ENTRY_PREFIX}'))='{ENTRY_PREFIX}' AND
 (length(value)-length(replace(value,{old_body},'')))/length({old_body})=2)<>1 THEN
 RAISE EXCEPTION 'successor missing exact ops boundary'; END IF;
 SELECT jsonb_agg(CASE WHEN left(value,length('{ENTRY_PREFIX}'))='{ENTRY_PREFIX}'
 THEN replace(value,{old_body},{new_body}) ELSE value END ORDER BY
 CASE WHEN left(value,length('{ENTRY_PREFIX}'))='{ENTRY_PREFIX}' THEN replace(value,{old_body},{new_body}) ELSE value END)
 INTO v_expected_entries FROM jsonb_array_elements_text(v_before->'boundaries'->v_login->v_field);
 IF v_post->'boundaries'->v_login->v_field IS DISTINCT FROM v_expected_entries THEN
 RAISE EXCEPTION 'successor uncontrolled boundary delta'; END IF;
 END LOOP;
 END LOOP;
 IF v_post->'boundaries'->'verdify_mcp_runtime_login' IS DISTINCT FROM v_before->'boundaries'->'verdify_mcp_runtime_login' THEN
 RAISE EXCEPTION 'successor changed MCP'; END IF;
 IF (SELECT jsonb_agg(value ORDER BY value->>'source',value->>'filename')
 FROM jsonb_array_elements({t.original_facts_sql()}->'ledger')
 WHERE NOT(value->>'source'='db/migrations' AND (value->>'seq')::int=270))
 IS DISTINCT FROM v_original->'ledger' THEN RAISE EXCEPTION 'successor changed full historical ledger rows'; END IF;
 IF ({t.original_facts_sql()}->'ordinary') IS DISTINCT FROM (v_original->'ordinary') OR
 ({t.original_facts_sql()}->'mcp') IS DISTINCT FROM (v_original->'mcp') THEN
 RAISE EXCEPTION 'successor changed historical seals'; END IF;
 IF (SELECT jsonb_agg(to_jsonb(r)-'rolpassword' ORDER BY oid) FROM pg_roles r) IS DISTINCT FROM v_roles OR
 (SELECT jsonb_agg(to_jsonb(m) ORDER BY oid) FROM pg_auth_members m) IS DISTINCT FROM v_members THEN
 RAISE EXCEPTION 'successor changed password-free role or membership metadata'; END IF;
 IF {receipts_sql()} IS DISTINCT FROM v_receipts THEN RAISE EXCEPTION 'successor changed prior target receipts'; END IF;
 {install}
 IF v_reviewed <> 'null'::jsonb AND v_post IS DISTINCT FROM v_reviewed THEN
 RAISE EXCEPTION 'successor unreviewed full post witness'; END IF;
 PERFORM set_config('verdify.cnpg_transition_result',jsonb_build_object('version','{VERSION}',
 'mode','{"install" if post is not None else "rollback-qualification"}','ddl_sha256','{digest(ddl.encode())}',
 'before_witness',v_before,'post_witness',v_post,'ops_before',v_ops_before,'ops_after',v_ops_after)::text,true);
END $successor270$;
SELECT current_setting('verdify.cnpg_transition_result');
{"COMMIT;" if post is not None else "ROLLBACK;"}
"""
    return sql


REFRESH_RAW_FIELDS = {
    "public.v_climate_merged": frozenset({"relfilenode", "relfrozenxid"}),
    "public.v_relay_stuck": frozenset({"relfilenode", "reltoastrelid", "relfrozenxid"}),
}


def validate_raw_lineage(previous, current):
    """Closed native PG16 nonconcurrent REFRESH physical delta, not normalization.

    Actual preserved 269 admission/current facts showed only these five fields.
    PG16.13 refresh_by_heap_swap/finish_heap_swap updates precisely their file,
    freeze horizon and (for the toasted relay view) TOAST link. Native completed
    jobs1014/1015 and their unchanged procedures establish the source path.
    Every other complete fact and ordered identity remains exact.
    """
    t.c0.require(set(previous) == set(current), "raw lineage group changed")
    changes = []
    for kind in previous:
        oldrows, newrows = previous[kind], current[kind]

        def identity(row):
            return row["raw_identity"] if kind == "triggers" else row["identity"]

        oldids = [identity(x) for x in oldrows]
        newids = [identity(x) for x in newrows]
        t.c0.require(
            oldids == newids and len(oldids) == len(set(oldids)), "raw lineage ordered object identities changed"
        )
        for old, new in zip(oldrows, newrows, strict=True):
            name = identity(old)
            allowed = REFRESH_RAW_FIELDS.get(name) if kind == "relations" else None
            if not allowed:
                t.c0.require(old == new, "unrelated raw lineage drift")
                continue
            t.c0.require(
                set(old) == set(new) and all(old[k] == new[k] for k in old if k != "native"),
                "refresh definition/native fact shape changed",
            )
            a, b = old["native"], new["native"]
            t.c0.require(set(a) == set(b) and a["relkind"] == b["relkind"] == "m", "refresh raw relation type changed")
            t.c0.require(all(a[k] == b[k] for k in a if k not in allowed), "unapproved raw refresh field drift")
            for field in sorted(allowed):
                t.c0.require(
                    all(
                        isinstance(value, str) and re.fullmatch(r"[0-9]+", value) and 0 < int(value) < 2**32
                        for value in [a[field], b[field]]
                    ),
                    "refresh native OID/XID type changed",
                )
                if a[field] != b[field]:
                    changes.append(
                        {
                            "kind": kind,
                            "identity": name,
                            "field": "native." + field,
                            "before": a[field],
                            "current": b[field],
                        }
                    )
    return changes


def validate_lineage(source, prior_install, before):
    """Historical source -> actual admission -> current complete predecessor.

    Complete catalog/semantic/body/ledger/seal fields remain identical. Only
    the two source-proven matview refreshes may alter their closed physical
    fields; every other full native fact remains exact. Both epochs and every
    permitted difference remain separate, never rewritten or called equal.
    """
    t.c0.require(
        prior_install["version"] == r.VERSION and prior_install["mode"] == "install",
        "genuine prior logical installation required",
    )
    t.c0.require(prior_install["ddl_sha256"] == digest(t.ddl(True)[0].encode()), "prior admission DDL changed")
    t.c0.compare(source, prior_install["before_witness"])
    t.validate_post(prior_install["before_witness"], prior_install["post_witness"])
    t.c0.checked(before, target=True)
    previous = prior_install["post_witness"]
    t.c0.require(set(before) == set(previous), "prior admission witness shape changed")
    t.c0.require(
        all(before[k] == previous[k] for k in before if k != "portability_native_facts"),
        "current admitted catalog/semantic/ledger/seal profile changed",
    )
    raw_delta = validate_raw_lineage(previous["portability_native_facts"], before["portability_native_facts"])
    return {
        "raw_delta": raw_delta,
        "original_source_to_prior_admission": True,
        "current_profile_unchanged": True,
        "prior_raw_sha256": digest(
            json.dumps(previous["portability_native_facts"], sort_keys=True, separators=(",", ":")).encode()
        ),
        "current_raw_sha256": digest(
            json.dumps(before["portability_native_facts"], sort_keys=True, separators=(",", ":")).encode()
        ),
        "historical_raw_equal": previous["portability_native_facts"] == before["portability_native_facts"],
        "successor_requires_exact_current_raw_before_after": True,
    }


def validate_prior_rows(rows):
    t.c0.require(
        isinstance(rows, list) and len(rows) == 2 and set(rows[0]) == {"ledger", "ordinary", "mcp"},
        "full native original rows and target receipts required",
    )
    original, receipts = rows
    t.c0.require(
        len(original["ledger"]) == 277 and len(original["ordinary"]) == 2 and len(original["mcp"]) == 1,
        "exact post269 original row counts required",
    )
    t.c0.require(
        len(receipts) == 3 and sorted(x["login_name"] for x in receipts) == sorted(t.LOGINS),
        "closed prior target receipt rows required",
    )
    t.c0.require(
        len({x["qualification_sha256"] for x in receipts}) == 1
        and all(t.transaction.is_hash(x["qualification_sha256"]) for x in receipts),
        "prior target qualification hash required",
    )
    t.c0.require(
        len(json.dumps(rows, ensure_ascii=False).encode()) <= 1_000_000, "fixed original row custody bound exceeded"
    )


def read_prior_rows(path):
    t.c0.require(path.is_file() and not path.is_symlink(), "regular native row custody required")
    with path.open("rb") as stream:
        raw = stream.read(1_000_001)
    t.c0.require(len(raw) <= 1_000_000, "native original row custody bound exceeded")
    lines = [line for line in raw.splitlines() if line.strip()]
    t.c0.require(len(lines) == 2, "exact two native row exports required")
    rows = [json.loads(line, object_pairs_hook=t.c0.boundary._pairs) for line in lines]
    validate_prior_rows(rows)
    return rows, digest(raw)


def read_record(path, *, mode="rollback-qualification"):
    t.c0.require(path.is_file() and not path.is_symlink(), "regular successor record required")
    maximum = 2 * t.c0.WITNESS_MAX_BYTES + 1024 * 1024
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    t.c0.require(len(raw) <= maximum, "complete successor record exceeds bound")
    value = json.loads(raw, object_pairs_hook=t.c0.boundary._pairs)
    t.c0.require(isinstance(value, dict), "successor record object required")
    for key in ("before_witness", "post_witness"):
        t.c0.require(
            len(json.dumps(value[key], ensure_ascii=False, separators=(",", ":")).encode()) <= t.c0.WITNESS_MAX_BYTES,
            "single witness bound exceeded",
        )
    t.c0.require(
        len(json.dumps([value["ops_before"], value["ops_after"]], ensure_ascii=False).encode()) <= 1024 * 1024,
        "fixed ops facts bound exceeded",
    )
    checked_record(value["before_witness"], value, mode=mode)
    return value, digest(raw)


def retained_sql(before, *, prior_rows, reviewed=None, qualification_sha=None):
    """Use the unchanged retained-session outer bootstrap/identity/locks.

    AS locks acquired before this savepoint survive a genuine qualification
    rollback; the full output is emitted before rollback and independently read.
    Installation commits only after the existing bootstrap custody guard.
    No new object is allocated, so new-table child-XID machinery is inapplicable.
    The complete existing pg_proc tuple fields/OID remain exact except prosrc.
    """
    sql = emit_sql(before, prior_rows=prior_rows, reviewed=reviewed, qualification_sha=qualification_sha)
    t.c0.require(sql.count("\nBEGIN;\n") == 1, "closed successor transaction required")
    sql = sql.replace("\nBEGIN;\n", "\nSAVEPOINT cnpg_target270_qualification;\n", 1)
    if reviewed is None:
        t.c0.require(sql.rstrip().endswith("ROLLBACK;"), "genuine qualification rollback required")
        sql = (
            sql.rstrip().removesuffix("ROLLBACK;")
            + "ROLLBACK TO SAVEPOINT cnpg_target270_qualification;\nRELEASE SAVEPOINT cnpg_target270_qualification;\n"
        )
    else:
        sql = r.install_custody_sql(sql)
    return sql


def outer_begin():
    # All original row timestamps and prior target receipt bytes are captured
    # before the savepoint and remain visible after a genuine rollback.
    return (
        r.outer_begin()
        + f"\nDO $successor_prior_rows$ BEGIN PERFORM set_config('verdify.cnpg270_original_rows',({t.original_facts_sql()})::text,true);\nPERFORM set_config('verdify.cnpg270_prior_receipts',({receipts_sql()})::text,true); END $successor_prior_rows$;\n"
    )


def rollback_proof(before):
    # The admitted table already exists: require byte-identical complete
    # predecessor, not the original first-admission table-absence assertion.
    witness = t.witness_select(before)
    select = witness[witness.index("SELECT jsonb_build_object(") :].rstrip().removesuffix(";")
    return (
        r.identity_sql()
        + t.transaction_witness_inputs_sql(before, None)
        + "\nDO $successor_rollback$ DECLARE v_actual jsonb; BEGIN\n"
        + select
        + f" INTO v_actual;\n IF ({t.original_facts_sql()}) IS DISTINCT FROM current_setting('verdify.cnpg270_original_rows')::jsonb OR ({receipts_sql()}) IS DISTINCT FROM current_setting('verdify.cnpg270_prior_receipts')::jsonb THEN RAISE EXCEPTION 'successor rollback changed historical rows or prior receipts'; END IF;\n IF v_actual IS DISTINCT FROM current_setting('verdify.cnpg_transition_expected_before')::jsonb THEN RAISE EXCEPTION 'successor rollback changed full predecessor'; END IF; END $successor_rollback$;\n"
        + r.custody_sql()
    )


def execute_qualification(args, source, prior, binding, roles, prior_rows):
    """One UID-bound rollback-only operation. No install/authentication action.

    Uses the existing native owner bridge, retained full-duplex Session and
    phase/total budgets. Source/inputs must already be hash-bound by main().
    """
    directory = args.receipt_dir
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    native_args = t.SimpleNamespace(**{k: binding[k] for k in ("cluster_uid", "pod", "pod_uid")})
    native_before = t.operator.read_target(native_args)
    t.c0.require(
        set(binding) == {"cluster_uid", "pod", "pod_uid", "operand_digest"}
        and binding["operand_digest"] == t.operator.DIGEST
        and native_before["cluster"]["status"]["currentPrimary"] == binding["pod"],
        "exact isolated primary binding required",
    )
    for key in ("cluster_uid", "pod_uid"):
        uuid.UUID(binding[key])
    provenance = r.operator_source(args.operator_source)
    (directory / "input-custody.json").write_text(
        json.dumps(
            {
                name: {
                    "path": str(getattr(args, name.replace("-", "_"))),
                    "sha256": getattr(args, name.replace("-", "_") + "_sha256"),
                }
                for name in ("original-source", "prior-install", "prior-row-custody", "binding", "source-roles")
            },
            indent=2,
        )
        + "\n"
    )
    raw = Path(__file__).read_bytes()
    committed = subprocess.check_output(
        ["git", "show", args.operator_source + ":scripts/cnpg-target270-successor.py"], cwd=ROOT
    )
    t.c0.require(raw == committed, "successor operator differs from reviewed source")
    provenance["module_sha256"]["cnpg-target270-successor.py"] = digest(raw)
    (directory / "operator-source.json").write_text(json.dumps(provenance, indent=2) + "\n")
    (directory / "native-binding-before.json").write_text(json.dumps(native_before, indent=2) + "\n")
    roles_before = r.role_export(binding, directory, "before")
    guard = 'test "$VERDIFY_REHEARSAL_POD_UID" = "$1" || exit 42; shift; unset PGOPTIONS PGSERVICE PGSERVICEFILE PGPASSWORD PGPASSFILE; exec timeout 900 "$@"'
    command = t.operator.kube(
        "exec",
        "-i",
        binding["pod"],
        "-c",
        "postgres",
        "--",
        "sh",
        "-c",
        guard,
        "uid-guard",
        binding["pod_uid"],
        "psql",
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-h",
        "/controller/run",
        "-U",
        "postgres",
        "-d",
        t.DATABASE,
    )
    session = r.Session(command, directory)
    qualified = False
    try:
        original = r.checked_custody(r.object_output(session.phase("begin", outer_begin()))[0])
        captured = session.phase("capture", r.capture_sql())
        t.c0.require(len(captured) <= t.c0.WITNESS_MAX_BYTES, "single complete capture bound exceeded")
        before = r.object_output(captured)[0]
        (directory / "actual-complete-predecessor.json").write_bytes(captured)
        lineage = validate_lineage(source, prior, before)
        mapping = t.c0.bootstrap_profile(source, before)
        t.role_parity.bootstrap_mapping(source, roles, before["bootstrap_identity"])
        role_before = t.role_parity.verify(roles, roles_before, mapping=mapping)
        (directory / "lineage.json").write_text(json.dumps(lineage, indent=2) + "\n")
        raw = session.phase("qualification", retained_sql(before, prior_rows=prior_rows))
        actual, actual_raw = r.object_output(raw)
        (directory / "actual-native-qualification.json").write_bytes(actual_raw)
        post = checked_record(before, actual)
        r.checked_custody(r.object_output(session.phase("rollback-custody", rollback_proof(before)))[0], original)
        final = (
            r.install_custody_sql("\nCOMMIT;\n").removesuffix("COMMIT;\n") + "ROLLBACK;\n" + r.bootstrap_return_sql()
        )
        returned = r.object_output(session.phase("outer-rollback-bootstrap", final))[0]
        t.c0.require(
            returned["session_user"] == returned["current_user"] == "postgres"
            and all(returned[k] == original[k] for k in ("pid", "backend_start", "postmaster_start")),
            "rollback privileged return custody changed",
        )
        roles_after = r.role_export(binding, directory, "after")
        t.c0.require(
            t.role_parity.verify(roles, roles_after, mapping=mapping) == role_before,
            "rollback password-free roles changed",
        )
        (directory / "independent-review.json").write_text(
            json.dumps(
                {
                    "full_checked_qualification": "PASS",
                    "actual_record_sha256": digest(actual_raw),
                    "source_migration_sha256": MIGRATION_SHA,
                    "source_identical_ops_ddl_sha256": actual["ddl_sha256"],
                    "native_successors": {k: v["native"] for k, v in post["boundaries"].items()},
                    "ordinary_password_authentication": False,
                    "target_installation": False,
                },
                indent=2,
            )
            + "\n"
        )
        qualified = True
    finally:
        outcome = session.close()
        outcome["rollback_qualification_verified"] = qualified
        (directory / "terminal.json").write_text(json.dumps(outcome, indent=2) + "\n")
        native_after = t.operator.read_target(native_args)
        (directory / "native-binding-after.json").write_text(json.dumps(native_after, indent=2) + "\n")
        a = next(x for x in native_before["pod"]["status"]["containerStatuses"] if x["name"] == "postgres")
        b = next(x for x in native_after["pod"]["status"]["containerStatuses"] if x["name"] == "postgres")
        t.c0.require(
            native_after["cluster"]["status"]["currentPrimary"] == binding["pod"]
            and a["containerID"] == b["containerID"]
            and a["restartCount"] == b["restartCount"],
            "native primary/container custody changed",
        )
    t.c0.require(
        qualified and outcome["native_exit"] == 0 and not outcome["commit_sent"],
        "native rollback qualification incomplete; no retry",
    )
    return outcome


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("original-source", "prior-install", "prior-row-custody"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--before-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reviewed", type=Path)
    parser.add_argument("--reviewed-sha256")
    parser.add_argument("--execute-qualification", action="store_true")
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--binding-sha256")
    parser.add_argument("--source-roles", type=Path)
    parser.add_argument("--source-roles-sha256")
    parser.add_argument("--operator-source")
    parser.add_argument("--receipt-dir", type=Path)
    args = parser.parse_args()
    source, source_hash = t.c0.read_witness(args.original_source)
    t.c0.require(source_hash == args.original_source_sha256, "original source custody mismatch")
    t.c0.require(args.prior_install.is_file() and not args.prior_install.is_symlink(), "regular prior install required")
    with args.prior_install.open("rb") as stream:
        prior_raw = stream.read(2 * t.c0.WITNESS_MAX_BYTES + 1025)
    t.c0.require(digest(prior_raw) == args.prior_install_sha256, "prior installation custody mismatch")
    prior, _ = r.record(prior_raw, "install")
    prior_rows, rows_hash = read_prior_rows(args.prior_row_custody)
    t.c0.require(rows_hash == args.prior_row_custody_sha256, "original row custody hash mismatch")
    if args.execute_qualification:
        t.c0.require(
            not any([args.before, args.output, args.reviewed, args.reviewed_sha256]),
            "rollback execution refuses mixed emit/install mode",
        )
        t.c0.require(
            all(
                [
                    args.binding,
                    args.binding_sha256,
                    args.source_roles,
                    args.source_roles_sha256,
                    args.operator_source,
                    args.receipt_dir,
                ]
            ),
            "complete rollback execution custody required",
        )
        binding, binding_hash = t.c0.read_witness(args.binding)
        t.c0.require(binding_hash == args.binding_sha256, "binding custody mismatch")
        t.c0.require(not args.source_roles.is_symlink(), "regular password-free roles required")
        roles = args.source_roles.read_bytes()
        t.c0.require(
            len(roles) <= 1_000_000 and digest(roles) == args.source_roles_sha256, "source role custody mismatch"
        )
        print(json.dumps(execute_qualification(args, source, prior, binding, roles.decode(), prior_rows)))
        return
    t.c0.require(args.before and args.before_sha256 and args.output, "complete SQL emission custody required")
    before, before_hash = t.c0.read_witness(args.before)
    t.c0.require(before_hash == args.before_sha256, "complete predecessor custody mismatch")
    validate_lineage(source, prior, before)
    reviewed = qualification_sha = None
    if args.reviewed:
        reviewed, qualification_sha = read_record(args.reviewed)
        t.c0.require(qualification_sha == args.reviewed_sha256, "reviewed successor custody mismatch")
    else:
        t.c0.require(args.reviewed_sha256 is None, "incomplete reviewed successor custody")
    sql = emit_sql(before, prior_rows=prior_rows, reviewed=reviewed, qualification_sha=qualification_sha)
    args.output.write_text(t.bootstrap_owner_sql(sql))


if __name__ == "__main__":
    main()
