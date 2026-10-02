"""Prepare fixed A/B physical-target admission; offline output is not recovery proof.

Only the two owning PITR names are supported. Consume independently qualified
logical installation, genuine physical provenance and native dataset witnesses.
Native execution/UID readback remains ROOT-owned; this script never connects.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("physical_logical", ROOT / "scripts/cnpg-target-runtime-transition.py")
t = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = t
spec.loader.exec_module(t)
pitr = t.load("render-cnpg-pitr-pair")
VERSION = "cnpg-physical-runtime-transition-v1"


def require(value, message):
    t.c0.require(value, message)


def validate_logical(source, before, rollback, install, inherited, logical_rows, rollback_sha):
    """Do not derive new trust from a copied receipt table or current digest."""
    t.c0.compare(source, before)
    qualified = t.checked_qualification(before, rollback)
    require(set(install) == set(rollback), "unexpected logical installation record")
    require(install["version"] == t.VERSION and install["mode"] == "install", "logical install not proven")
    require(
        install["ddl_sha256"] == rollback["ddl_sha256"] and install["before_witness"] == before,
        "logical installation lineage mismatch",
    )
    installed = t.validate_installed_post(before, qualified, install["post_witness"])
    require(inherited == installed, "physical predecessor differs from actual logical installed catalog")
    require(t.transaction.is_hash(rollback_sha), "logical rollback custody hash required")
    expected = [[login, qualified["boundaries"][login]["native"], rollback_sha] for login in sorted(t.LOGINS)]
    require(logical_rows == expected, "copied logical receipt rows differ from independently qualified source")


POST270_VERSION = "cnpg-physical-post270-logical-lineage-v1"
POST270_KEYS = {
    "rollback",
    "install",
    "prior_rows",
    "installed_rows",
    "namespace_custody",
    "source_roles",
    "installed_roles",
}
POST270_ROLLBACK_SHA = "75a78e501f8f71f443307c9e55420a32c6b92eb788aaa873b31037db4c48b439"
POST270_INSTALL_SHA = "74b8ddedbdf57de1b657c2933c78bb530c0aa5e5b63875801652418a827da3fe"
POST270_NATIVE_LEDGER_ROW = {
    "seq": 270,
    "sha256": "672d1afa92e37f243d5893fbd57cd2b84e86ea19c1c79d3975c04dfd39663532",
    "source": "db/migrations",
    "filename": "db/migrations/270-facility-safe-ops-projection.sql",
    "applied_at": "2026-10-02T06:58:54.474684+00:00",
    "applied_by": "verdify",
    "duration_ms": 54951,
    "stamp_method": "runner",
}
POST270_FROZEN_INPUTS = {
    "installed_rows": "cfc9615125683124c92e803994d4dc2e19530dbc81ef9cd816dece88ae99c731",
    "prior_rows": "33cbcda2257994abf79446020eba861f84a2c3ca00480b0c10910de4b25ac329",
    "source_roles": "7b2da9d7f57db51c7d6fe25515e337bc35bb5f79f69aff943321d7d383eeaf69",
    "namespace_custody": "2b2ab98714256daf72272277318c5c705e5f3feca17c9929bdfc9fb9328f52c0",
}


def successor_module():
    return t.load("cnpg-target270-successor")


def read_post270_lineage(path, expected_sha):
    """Hash-bound original records, never a projected artifact labeled genuine."""
    import json

    manifest, sha = t.c0.read_witness(path)
    require(sha == expected_sha, "post270 manifest custody mismatch")
    require(
        set(manifest) == {"version", "inputs"} and manifest["version"] == POST270_VERSION,
        "closed post270 lineage manifest required",
    )
    require(set(manifest["inputs"]) == POST270_KEYS, "complete post270 lineage inputs required")
    d, values = successor_module(), {}
    for key, fact in manifest["inputs"].items():
        require(
            set(fact) == {"path", "sha256"} and t.transaction.is_hash(fact["sha256"]),
            "closed post270 input binding required",
        )
        target = Path(fact["path"])
        require(target.is_file() and not target.is_symlink(), "regular post270 custody file required")
        if key in {"rollback", "install"}:
            value, actual_sha = d.read_record(target, mode="rollback-qualification" if key == "rollback" else "install")
        elif key == "prior_rows":
            value, actual_sha = d.read_prior_rows(target)
        else:
            raw = target.read_bytes()
            require(len(raw) <= 1_000_000, "post270 small custody bound exceeded")
            actual_sha = hashlib.sha256(raw).hexdigest()
            value = raw.decode() if key.endswith("roles") else json.loads(raw, object_pairs_hook=t.c0.boundary._pairs)
        require(actual_sha == fact["sha256"], "post270 input custody mismatch")
        if key in {"rollback", "install"}:
            require(
                actual_sha == (POST270_ROLLBACK_SHA if key == "rollback" else POST270_INSTALL_SHA),
                "unreviewed post270 native record",
            )
        if key in POST270_FROZEN_INPUTS:
            require(actual_sha == POST270_FROZEN_INPUTS[key], "historical post270 input changed")
        values[key] = value
    return values


def validate_post270_logical(source, before, rollback, install, inherited, logical_rows, rollback_sha, successor):
    """The real retained269 -> genuine270 chain; original facts remain separate."""
    import copy

    d = successor_module()
    t.c0.compare(source, before)
    qualified = t.checked_qualification(before, rollback, retained_session=True)
    require(
        set(install) == set(rollback) and install["version"] == d.r.VERSION and install["mode"] == "install",
        "original retained logical install not proven",
    )
    require(
        install["ddl_sha256"] == rollback["ddl_sha256"] and install["before_witness"] == before,
        "original retained logical lineage mismatch",
    )
    admitted = t.validate_installed_post(before, qualified, install["post_witness"])
    require(t.transaction.is_hash(rollback_sha), "original retained rollback custody required")
    prior = successor["prior_rows"]
    d.validate_prior_rows(prior)
    require(
        [
            [row["login_name"], row["boundary_sha256"].removeprefix("\\x"), row["qualification_sha256"]]
            for row in prior[1]
        ]
        == [[login, qualified["boundaries"][login]["native"], rollback_sha] for login in sorted(t.LOGINS)],
        "original269 full receipt lineage mismatch",
    )
    require(admitted == install["post_witness"], "original269 post custody changed")
    native_rollback, native_install = successor["rollback"], successor["install"]
    custody = successor["namespace_custody"]
    for record in (native_rollback, native_install):
        predecessor = record["before_witness"]
        proof = custody if predecessor["namespaces"] != admitted["namespaces"] else None
        d.validate_lineage(source, install, predecessor, namespace_custody=proof)
        d.checked_record(predecessor, record, mode="install" if record is native_install else "rollback-qualification")
    proof = (
        custody
        if native_install["before_witness"]["namespaces"] != native_rollback["before_witness"]["namespaces"]
        else None
    )
    expected, _ = d.installation_projection(native_install["before_witness"], native_rollback, namespace_custody=proof)
    require(native_install["post_witness"] == expected, "actual270 install differs from reviewed current projection")
    rows = successor["installed_rows"]
    require(set(rows) == {"original", "qualified"}, "full actual270 row custody required")
    original = copy.deepcopy(rows["original"])
    added = [row for row in original["ledger"] if row["source"] == "db/migrations" and row["seq"] == 270]
    require(len(added) == 1 and len(original["ledger"]) == 278, "exact270 ledger successor required")
    new = added[0]
    require(new == POST270_NATIVE_LEDGER_ROW, "actual270 complete native ledger row changed")
    require(
        new["filename"] == d.MIGRATION
        and new["sha256"] == d.MIGRATION_SHA
        and new["stamp_method"] == "runner"
        and new["applied_by"] == "verdify"
        and type(new["duration_ms"]) is int
        and new["duration_ms"] >= 0
        and isinstance(new["applied_at"], str)
        and bool(new["applied_at"]),
        "270 native ledger stamp changed",
    )
    original["ledger"].remove(new)
    require(original == prior[0], "original277 full rows or historical seals changed")
    expected_rows = copy.deepcopy(prior[1])
    for row in expected_rows:
        row["boundary_sha256"] = "\\x" + expected["boundaries"][row["login_name"]]["native"]
        row["qualification_sha256"] = POST270_ROLLBACK_SHA
    require(rows["qualified"] == expected_rows, "actual270 receipt fields/timestamps changed")
    mapping = t.c0.bootstrap_profile(source, native_install["before_witness"])
    t.role_parity.verify(successor["source_roles"], successor["installed_roles"], mapping=mapping)
    require(set(inherited) == set(expected), "inherited270 full witness shape changed")
    require(
        all(inherited[key] == expected[key] for key in expected if key != "portability_native_facts"),
        "inherited270 catalog/ledger/seals/roles/namespaces/body drift",
    )
    t.c0.checked(inherited, target=True)
    if inherited["portability_native_facts"] != expected["portability_native_facts"]:
        d.validate_raw_lineage(
            expected["portability_native_facts"], inherited["portability_native_facts"], namespace_custody=custody
        )
    require(
        logical_rows
        == [[login, expected["boundaries"][login]["native"], POST270_ROLLBACK_SHA] for login in sorted(t.LOGINS)],
        "copied270 receipts differ from genuine source chain",
    )


def dataset_sql(profile):
    """Capture actual native counts/time/owners, never sampled or synthetic rows."""
    require(profile in (pitr.SOURCE, *t.PHYSICAL_TARGETS), "unsupported native dataset source/profile")
    return f"""\\set ON_ERROR_STOP on
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
-- Public view functions resolve their source-owned public dependencies.
-- pg_catalog stays first; pg_temp cannot shadow those public routines.
SET LOCAL search_path=pg_catalog,public,pg_temp;
SET LOCAL timezone='UTC';
SET LOCAL statement_timeout='120s';
SET LOCAL lock_timeout='2s';
DO $physical_dataset$
DECLARE relation record; column_row record; row_count bigint; endpoints jsonb;
        ranges jsonb; inventory jsonb := '[]'::jsonb; owners jsonb;
BEGIN
 IF current_database()<>'verdify_rehearsal' OR current_setting('server_version_num')::int<>160013
    OR current_setting('cluster_name')<>'{profile}' OR pg_is_in_recovery()
    OR inet_client_addr() IS NOT NULL OR current_user<>session_user OR current_user<>'verdify'
    OR (SELECT extversion FROM pg_extension WHERE extname='timescaledb') IS DISTINCT FROM '2.25.2' THEN
   RAISE EXCEPTION 'native physical dataset refuses target/session';
 END IF;
 IF EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname='public' AND c.relkind='f') THEN
   RAISE EXCEPTION 'native physical dataset refuses external table endpoints';
 END IF;
 FOR relation IN SELECT c.oid, n.nspname, c.relname FROM pg_class c
     JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S') ORDER BY c.relname LOOP
   EXECUTE format('SELECT count(*) FROM %I.%I', relation.nspname, relation.relname) INTO row_count;
   ranges := '{{}}'::jsonb;
   FOR column_row IN SELECT attname FROM pg_attribute WHERE attrelid=relation.oid
       AND attnum>0 AND NOT attisdropped AND atttypid IN ('timestamp'::regtype,'timestamptz'::regtype)
       ORDER BY attnum LOOP
     EXECUTE format('SELECT jsonb_build_array(min(%I)::text,max(%I)::text) FROM %I.%I',
                    column_row.attname,column_row.attname,relation.nspname,relation.relname) INTO endpoints;
     ranges := ranges || jsonb_build_object(column_row.attname,endpoints);
   END LOOP;
   inventory := inventory || jsonb_build_array(jsonb_build_object(
       'relation',format('%I.%I',relation.nspname,relation.relname),'count',row_count,'time_ranges',ranges));
 END LOOP;
 SELECT jsonb_agg(jsonb_build_object(
    'parent',format('%I.%I',h.schema_name,h.table_name),'parent_owner',pg_get_userbyid(parent.relowner),
    'chunk',format('%I.%I',c.schema_name,c.table_name),'chunk_owner',pg_get_userbyid(chunk.relowner),
    'compressed',CASE WHEN c.compressed_chunk_id IS NOT NULL THEN format('%I.%I',cc.schema_name,cc.table_name) END,
    'compressed_owner',pg_get_userbyid(compressed.relowner)) ORDER BY h.schema_name,h.table_name,c.schema_name,c.table_name)
 INTO owners FROM _timescaledb_catalog.hypertable h JOIN _timescaledb_catalog.chunk c ON c.hypertable_id=h.id
 LEFT JOIN pg_class parent ON parent.oid=to_regclass(format('%I.%I',h.schema_name,h.table_name))
 LEFT JOIN pg_class chunk ON chunk.oid=to_regclass(format('%I.%I',c.schema_name,c.table_name))
 LEFT JOIN _timescaledb_catalog.chunk cc ON cc.id=c.compressed_chunk_id
 LEFT JOIN pg_class compressed ON compressed.oid=to_regclass(
    CASE WHEN cc.id IS NOT NULL THEN format('%I.%I',cc.schema_name,cc.table_name) END)
 WHERE h.schema_name='public' AND NOT c.dropped;
 PERFORM set_config('verdify.physical_dataset',jsonb_build_object(
     'schema','cnpg-physical-data-parity-v1','database',current_database(),
     'relations',inventory,'timescale_owners',coalesce(owners,'[]'::jsonb))::text,true);
END $physical_dataset$;
SELECT current_setting('verdify.physical_dataset');
COMMIT;
"""


def dataset_peer_sql(profile):
    """Use the same exact peer/owner bridge while retaining the readonly snapshot."""
    sql = dataset_sql(profile)
    readonly_begin = "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;"
    require(sql.count(readonly_begin) == 1 and "\nBEGIN;\n" not in sql, "unexpected dataset transaction shape")
    owner = sql.replace(readonly_begin, "BEGIN;", 1)
    wrapped = t.bootstrap_owner_sql(owner, physical_target=None if profile == pitr.SOURCE else profile)
    require(wrapped.count("\nBEGIN;\n") == 1, "unexpected peer dataset transaction shape")
    return wrapped.replace("\nBEGIN;\n", "\n" + readonly_begin + "\n", 1)


def validate_dataset(source, restored, catalog=None):
    require(source == restored, "native physical dataset/count/time/owner drift")
    require(
        set(source) == {"schema", "database", "relations", "timescale_owners"}
        and source["schema"] == "cnpg-physical-data-parity-v1"
        and source["database"] == t.DATABASE,
        "unexpected native dataset witness",
    )
    require(isinstance(source["relations"], list) and source["relations"], "missing native data relation inventory")
    identities = set()
    for row in source["relations"]:
        require(set(row) == {"relation", "count", "time_ranges"}, "incomplete native count/time relation")
        require(
            row["relation"].startswith("public.") and row["relation"] not in identities,
            "ambiguous public relation identity",
        )
        require(
            type(row["count"]) is int and row["count"] >= 0 and isinstance(row["time_ranges"], dict),
            "invalid count/time inventory",
        )
        identities.add(row["relation"])
        for column, bounds in row["time_ranges"].items():
            require(
                isinstance(column, str) and column and isinstance(bounds, list) and len(bounds) == 2,
                "malformed native timestamp range",
            )
            require(
                (bounds == [None, None]) or all(isinstance(value, str) for value in bounds),
                "incomplete native timestamp endpoints",
            )
    require(
        isinstance(source["timescale_owners"], list) and source["timescale_owners"],
        "missing actual Timescale parent/chunk/compressed-owner inventory",
    )
    if catalog is not None:
        expected = {row[1] for row in catalog if row[0] == "relation" and row[1].startswith("public.")}
        require(identities == expected, "native dataset inventory omits or adds public catalog relations")
    keys = set()
    for row in source["timescale_owners"]:
        require(
            set(row) == {"parent", "parent_owner", "chunk", "chunk_owner", "compressed", "compressed_owner"},
            "incomplete Timescale ownership row",
        )
        require(
            row["chunk"] not in keys and row["parent_owner"] == row["chunk_owner"] == "verdify",
            "Timescale parent/chunk owner drift",
        )
        require(
            (row["compressed"] is None and row["compressed_owner"] is None)
            or (isinstance(row["compressed"], str) and row["compressed_owner"] == "verdify"),
            "Timescale compressed owner drift",
        )
        keys.add(row["chunk"])


def validate_physical(profile, binding, source_cluster, backup, markers, recovery, source_data, target_data):
    require(profile in t.PHYSICAL_TARGETS, "unsupported physical profile")
    require(
        set(binding) == {"namespace", "cluster", "cluster_uid", "pod", "pod_uid", "operand_digest"},
        "unexpected physical binding",
    )
    require(
        binding["namespace"] == pitr.NS and binding["cluster"] == profile, "physical binding namespace/profile mismatch"
    )
    pitr.uid(binding["cluster_uid"])
    pitr.uid(binding["pod_uid"])
    require(
        binding["cluster_uid"] != pitr.SOURCE_UID and binding["pod_uid"] != markers["primary_pod_uid"],
        "physical target must be a new identity",
    )
    require(re.fullmatch(re.escape(profile) + r"-[1-9]\d*", binding["pod"]) is not None, "wrong exact new primary name")
    require(binding["operand_digest"] == t.operator.DIGEST, "unqualified physical operand")
    # The supplied marker/backups must pass the independently source-owned pair
    # renderer too. Raw capture SHA/content validation happens in CLI first.
    pitr.render(source_cluster, backup, markers, recovery.get("scheduled_backup"))
    label = "A" if profile.endswith("-a") else "B"
    expected = [markers["markers"][key]["marker_id"] for key in (("A",) if label == "A" else ("A", "B"))]
    require(
        set(recovery)
        == {
            "schema",
            "binding",
            "source_uid",
            "backup_uid",
            "backup_id",
            "source_timeline",
            "target_time",
            "native_target_reached_sha256",
            "native_archive_inventory_sha256",
            "sentinel_capture_sha256",
            "marker_ids",
            "scheduled_backup",
        },
        "unexpected physical provenance record",
    )
    require(
        recovery["schema"] == "cnpg-physical-target-provenance-v1" and recovery["binding"] == binding,
        "physical provenance identity mismatch",
    )
    require(
        recovery["source_uid"] == pitr.SOURCE_UID
        and recovery["backup_uid"] == backup["metadata"]["uid"]
        and recovery["backup_id"] == backup["status"]["backupId"]
        and recovery["source_timeline"] == markers["timeline"]
        and recovery["target_time"] == markers["targets"][label],
        "physical source/backup/timeline/target mismatch",
    )
    require(recovery["marker_ids"] == expected, "actual recovered A-only/A+B marker inventory differs")
    for key in ("native_target_reached_sha256", "native_archive_inventory_sha256", "sentinel_capture_sha256"):
        require(t.transaction.is_hash(recovery[key]), "missing native physical proof capture hash")
    validate_dataset(source_data, target_data)


def validate_sentinel_capture(capture, recovery, markers):
    require(
        set(capture) == {"binding", "database", "server_version_num", "cluster_name", "pg_is_in_recovery", "markers"},
        "unexpected native sentinel capture",
    )
    binding = recovery["binding"]
    require(
        capture["binding"] == binding
        and capture["database"] == "rehearsal_bootstrap"
        and capture["server_version_num"] == 160013
        and capture["cluster_name"] == binding["cluster"]
        and capture["pg_is_in_recovery"] is False,
        "native recovered sentinel session mismatch",
    )
    expected = []
    for label in ("A", "B", "C"):
        marker = markers["markers"][label]
        require(t.transaction.is_hash(marker.get("payload_sha256")), "actual original marker payload hash required")
        if marker["marker_id"] in recovery["marker_ids"]:
            expected.append({"marker_id": marker["marker_id"], "payload_sha256": marker["payload_sha256"]})
    require(
        capture["markers"] == sorted(expected, key=lambda row: row["marker_id"]),
        "native A-only/A+B sentinel row or payload mismatch",
    )


def checked_physical_qualification(before, record, profile):
    require(
        isinstance(record, dict) and set(record) == {"version", "mode", "ddl_sha256", "before_witness", "post_witness"},
        "unexpected physical rollback record",
    )
    require(
        record["version"] == VERSION and record["mode"] == "rollback-qualification",
        "not an actual physical rollback qualification",
    )
    ddl, _ = t.ddl(bool(before.get("bootstrap_grantor_profile")), physical_target=profile)
    require(
        record["ddl_sha256"] == t.digest(ddl.encode()) and record["before_witness"] == before,
        "physical rollback source/predecessor mismatch",
    )
    t.validate_post(before, record["post_witness"], physical_target=profile)
    return record["post_witness"]


def client_bindings(profile, binding, installation, reviewed, reviewed_sha):
    """Exact service/login bindings only; these do not prove real authentication."""
    require(
        binding.get("namespace") == pitr.NS and binding.get("cluster") == profile,
        "physical client binding scope mismatch",
    )
    pitr.uid(binding["cluster_uid"])
    require(binding["cluster_uid"] != pitr.SOURCE_UID, "original source cannot become a physical client target")
    post = checked_physical_qualification(installation["before_witness"], reviewed, profile)
    require(
        installation["version"] == VERSION
        and installation["mode"] == "install"
        and installation["ddl_sha256"] == reviewed["ddl_sha256"]
        and installation["before_witness"] == reviewed["before_witness"],
        "physical installation missing/mismatched",
    )
    t.validate_installed_post(
        installation["before_witness"], post, installation["post_witness"], physical_target=profile
    )
    require(t.transaction.is_hash(reviewed_sha), "reviewed physical result custody required")
    return {
        consumer: {
            "namespace": pitr.NS,
            "cluster": profile,
            "cluster_uid": binding["cluster_uid"],
            "host": profile + "-rw." + pitr.NS + ".svc.cluster.local",
            "database": t.DATABASE,
            "login": login,
            "physical_qualification_sha256": reviewed_sha,
            "required_proof": "actual ordinary-role password/SCRAM pool startup and baked-image hot queries",
        }
        for consumer, login in zip(("api", "ingestor", "mcp"), t.LOGINS, strict=True)
    }


INPUT_KEYS = (
    "original_source",
    "logical_before",
    "logical_rollback",
    "logical_install",
    "physical_before",
    "logical_rows",
    "binding",
    "source_cluster",
    "backup",
    "markers",
    "recovery",
    "source_data",
    "target_data",
)


def input_arguments(parser):
    for key in INPUT_KEYS:
        parser.add_argument("--" + key.replace("_", "-"), type=Path, required=True)
        parser.add_argument("--" + key.replace("_", "-") + "-sha256", required=True)
    parser.add_argument("--post270-lineage", type=Path)
    parser.add_argument("--post270-lineage-sha256")
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--physical-proof-dir", type=Path, required=True)
    parser.add_argument("--reviewed-physical", type=Path)
    parser.add_argument("--reviewed-physical-sha256")


def qualified_inputs(args):
    data = {}
    successor = None
    if getattr(args, "post270_lineage", None):
        successor = read_post270_lineage(args.post270_lineage, args.post270_lineage_sha256)
    else:
        require(not getattr(args, "post270_lineage_sha256", None), "incomplete post270 lineage binding")
    for key in INPUT_KEYS:
        if key in {"logical_rollback", "logical_install"}:
            value, sha = t.read_transition_record(
                getattr(args, key),
                version="cnpg-native-retained-session-transition-v1" if successor is not None else t.VERSION,
                mode=("savepoint-rollback-qualification" if successor is not None else "rollback-qualification")
                if key == "logical_rollback"
                else "install",
            )
        else:
            value, sha = t.c0.read_witness(getattr(args, key))
        require(sha == getattr(args, key + "_sha256"), "physical input custody mismatch")
        data[key] = value
    pitr.validate_captures(data["markers"], args.captures)
    for key, filename in (
        ("native_target_reached_sha256", "target-reached.log"),
        ("native_archive_inventory_sha256", "archive-inventory.json"),
        ("sentinel_capture_sha256", "recovered-sentinels.json"),
    ):
        raw = (args.physical_proof_dir / filename).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == data["recovery"][key], "raw physical capture hash mismatch")
    validator = validate_logical if successor is None else validate_post270_logical
    validator(
        data["original_source"],
        data["logical_before"],
        data["logical_rollback"],
        data["logical_install"],
        data["physical_before"],
        data["logical_rows"],
        args.logical_rollback_sha256,
        **({"successor": successor} if successor is not None else {}),
    )
    validate_dataset(data["source_data"], data["target_data"], data["physical_before"]["portable_catalog"])
    import json

    validate_sentinel_capture(
        json.loads((args.physical_proof_dir / "recovered-sentinels.json").read_bytes()),
        data["recovery"],
        data["markers"],
    )
    validate_physical(
        args.profile,
        data["binding"],
        data["source_cluster"],
        data["backup"],
        data["markers"],
        data["recovery"],
        data["source_data"],
        data["target_data"],
    )
    post, qualification_sha = None, None
    if args.reviewed_physical:
        record, qualification_sha = t.read_transition_record(
            args.reviewed_physical, version=VERSION, mode="rollback-qualification"
        )
        require(qualification_sha == args.reviewed_physical_sha256, "physical review custody mismatch")
        post = checked_physical_qualification(data["physical_before"], record, args.profile)
    else:
        require(not args.reviewed_physical_sha256, "incomplete physical reviewed qualification")
    return data, post, qualification_sha


def main():
    if sys.argv[1:2] == ["emit-dataset-sql"]:
        parser = argparse.ArgumentParser(description="Emit native read-only full count/time/owner collector")
        parser.add_argument("emit-dataset-sql")
        parser.add_argument("--profile", choices=(pitr.SOURCE, *t.PHYSICAL_TARGETS), required=True)
        parser.add_argument("--output", type=Path, required=True)
        args = parser.parse_args()
        with args.output.open("x") as stream:
            stream.write(dataset_peer_sql(args.profile))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=t.PHYSICAL_TARGETS, required=True)
    input_arguments(parser)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data, post, qualification_sha = qualified_inputs(args)
    sql = t.emit_sql(
        data["physical_before"],
        reviewed_post=post,
        qualification_sha256=qualification_sha,
        physical_target=args.profile,
        logical_receipts=data["logical_rows"],
    )
    with args.output.open("x") as stream:
        stream.write(t.bootstrap_owner_sql(sql, physical_target=args.profile))


if __name__ == "__main__":
    main()
