"""Render guarded clone-only owned-row inverse SQL; never executes it.

Witness must come from ROOT's exact UID-bound owner readback after clients stop.
Original sequence/study and chunk custody remain separately required.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "committed_spool", Path(__file__).with_name("qualify-ingestor-committed-spool.py")
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def render(manifest, witness):
    probe.validate_manifest(manifest)
    profile = probe.clone_profile(witness)
    assert witness["database_oid"] == 16447 and witness["clients_stopped"] is True
    assert witness["before_fixture_rows"] == 0
    assert witness["manifest_run_id"] == manifest["run_id"]
    expected_tables = {"climate", "diagnostics", "equipment_state"}
    before = witness["before_year2000_chunks"]
    chunks = witness["added_chunks"]
    targets = witness["target_chunks"]
    assert {chunk["table"] for chunk in targets} == expected_tables and len(targets) == 3
    before_by_name = {chunk["name"]: chunk for chunk in before}
    assert len(before_by_name) == len(before)
    target_by_name = {chunk["name"]: chunk for chunk in targets}
    assert len(target_by_name) == len(targets)
    assert {chunk["name"] for chunk in chunks} == set(target_by_name) - set(before_by_name)
    assert len({chunk["name"] for chunk in chunks}) == len(chunks)
    assert all(chunk == target_by_name[chunk["name"]] for chunk in chunks)
    for chunk in before:
        assert chunk["table"] in expected_tables
        assert re.fullmatch(r"_timescaledb_internal\._hyper_[0-9]+_[0-9]+_chunk", chunk["name"])
        assert type(chunk["row_count"]) is int and chunk["row_count"] >= 0
        assert re.fullmatch(r"[0-9a-f]{64}", chunk["row_sha256"])
    for chunk in targets:
        assert re.fullmatch(r"_timescaledb_internal\._hyper_[0-9]+_[0-9]+_chunk", chunk["name"])
        assert chunk["owned_rows"] == 1
        prior = before_by_name.get(chunk["name"])
        assert chunk["other_rows"] == (prior["row_count"] if prior else 0)
        if prior:
            assert all(chunk[k] == prior[k] for k in ("table", "range_start", "range_end"))
        assert chunk["range_start"].startswith("1999-") or chunk["range_start"].startswith("2000-")
        assert chunk["range_end"].startswith("2000-")
    hashes = witness["row_sha256"]
    assert set(hashes) == {"climate_source_events", "observational_source_events", "equipment_state_source_receipts"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes.values())
    trigger = witness["immutable_trigger"]
    assert trigger["enabled"] in {"O", "A"}
    assert re.fullmatch(r"[0-9a-f]{64}", trigger["function_sha256"])
    climate, diagnostics, equipment = (manifest["events"][k] for k in ("climate", "diagnostics", "equipment"))
    ts = climate["event"]["row"]["ts"]
    instant = datetime.fromisoformat(ts)
    for chunk in targets:
        start, end = datetime.fromisoformat(chunk["range_start"]), datetime.fromisoformat(chunk["range_end"])
        assert start <= instant < end and end - start <= timedelta(days=31)
    assert diagnostics["event"]["observed_at"] == equipment["event"]["source_observed_through"] == ts
    runtime = str(uuid.UUID(climate["event"]["runtime_instance_id"]))
    assert diagnostics["event"]["runtime_instance_id"] == equipment["event"]["source_runtime_instance_id"] == runtime
    ids = {
        "climate_source_events": ("event_id", climate["identity"]),
        "observational_source_events": ("event_id", diagnostics["identity"]),
        "equipment_state_source_receipts": ("receipt_id", equipment["identity"]),
    }
    guards = []
    for table, (key, identity) in ids.items():
        identity = str(uuid.UUID(identity))
        guards.append(
            f"(SELECT count(*)=1 AND bool_and(encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex')={literal(hashes[table])}) FROM public.{table} t WHERE {key}={literal(identity)}::uuid)"
        )
    row_guard = " AND ".join(guards)
    chunk_guards = " AND ".join(
        f"(SELECT count(*)={1 + chunk['other_rows']} FROM {chunk['name']})" for chunk in targets
    )
    equipment_id = literal(equipment["identity"])
    qts, qruntime = literal(ts), literal(runtime)
    row_guard += f" AND EXISTS(SELECT 1 FROM public.climate_source_events WHERE event_id={literal(climate['identity'])}::uuid AND greenhouse_id='vallery' AND source_ts={qts}::timestamptz AND source_runtime_instance_id={qruntime}::uuid AND source_connection_generation=7 AND climate_payload={literal(json.dumps({'temp_avg': 70.0}))}::jsonb AND sample_provenance={literal(json.dumps(climate['event']['sample_provenance']))}::jsonb)"
    row_guard += f" AND EXISTS(SELECT 1 FROM public.observational_source_events WHERE event_id={literal(diagnostics['identity'])}::uuid AND kind='diagnostics' AND greenhouse_id='vallery' AND source_ts={qts}::timestamptz AND source_runtime_instance_id={qruntime}::uuid AND source_connection_generation=7 AND event_payload={literal(json.dumps(diagnostics['event']['payload']))}::jsonb)"
    body = [
        "BEGIN; SET LOCAL statement_timeout='30s'; SET LOCAL lock_timeout='5s';",
        f"DO $guard$ BEGIN IF current_user<>'postgres' OR session_user<>'postgres' OR current_database()<>'verdify_rehearsal' OR current_setting('cluster_name')<>{literal(profile['cluster'])} OR pg_is_in_recovery() OR (SELECT oid FROM pg_database WHERE datname=current_database())<>16447 THEN RAISE EXCEPTION 'clone inverse authority refused';END IF; END $guard$;",
        "LOCK TABLE public.climate_source_events, public.observational_source_events, public.equipment_state_source_receipts, public.climate, public.diagnostics, public.equipment_state IN ACCESS EXCLUSIVE MODE;",
        f"DO $guard$ BEGIN IF NOT ({row_guard} AND {chunk_guards}) OR EXISTS(SELECT 1 FROM public.experiment_v2_outcome_source_bindings WHERE subject_id={equipment_id}::uuid) OR NOT EXISTS(SELECT 1 FROM public.equipment_state_source_receipts WHERE receipt_id={equipment_id}::uuid AND source_connection_generation=7 AND source_runtime_instance_id={qruntime}::uuid AND source_observed_through={qts}::timestamptz AND gap_requested AND gap_before AND gap_reason='initial_receipt' AND source_sequence=1 AND event_count=1 AND firmware_revision='s2-qualification') THEN RAISE EXCEPTION 'owned fixture/chunk/experiment guard refused'; END IF; END $guard$;",
        f"DO $guard$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.equipment_state_source_receipts'::regclass AND tgname='trg_equipment_state_source_receipts_immutable' AND tgenabled={literal(trigger['enabled'])} AND encode(sha256(convert_to(pg_get_functiondef(tgfoid),'UTF8')),'hex')={literal(trigger['function_sha256'])}) THEN RAISE EXCEPTION 'original immutable trigger custody refused';END IF; END $guard$;",
        "ALTER TABLE public.equipment_state_source_receipts DISABLE TRIGGER trg_equipment_state_source_receipts_immutable;",
        f"DELETE FROM public.equipment_state_source_receipts WHERE receipt_id={equipment_id}::uuid;",
        "ALTER TABLE public.equipment_state_source_receipts "
        + ("ENABLE ALWAYS" if trigger["enabled"] == "A" else "ENABLE")
        + " TRIGGER trg_equipment_state_source_receipts_immutable;",
        f"DELETE FROM public.climate_source_events WHERE event_id={literal(climate['identity'])}::uuid;",
        f"DELETE FROM public.observational_source_events WHERE event_id={literal(diagnostics['identity'])}::uuid;",
        f"DELETE FROM public.climate WHERE ts={qts}::timestamptz AND greenhouse_id='vallery' AND temp_avg=70.0;",
        f"DELETE FROM public.diagnostics WHERE ts={qts}::timestamptz AND greenhouse_id='vallery' AND firmware_version='s2-qualification';",
        f"DELETE FROM public.equipment_state WHERE ts={qts}::timestamptz AND greenhouse_id='vallery' AND equipment='fan1' AND state=false;",
        f"DO $guard$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='public.equipment_state_source_receipts'::regclass AND tgname='trg_equipment_state_source_receipts_immutable' AND tgenabled={literal(trigger['enabled'])} AND encode(sha256(convert_to(pg_get_functiondef(tgfoid),'UTF8')),'hex')={literal(trigger['function_sha256'])}) THEN RAISE EXCEPTION 'immutable trigger restoration failed';END IF; END $guard$;",
    ]
    for chunk in chunks:
        body += [
            f"DO $guard$ BEGIN IF EXISTS(SELECT 1 FROM {chunk['name']}) THEN RAISE EXCEPTION 'nonempty owned chunk refused';END IF; END $guard$;",
            "DO $guard$ DECLARE dropped text[]; BEGIN SELECT array_agg(x::text ORDER BY x::text) INTO dropped FROM drop_chunks("
            + literal("public." + chunk["table"])
            + "::regclass,older_than=>"
            + literal(chunk["range_end"])
            + "::timestamptz,newer_than=>"
            + literal(chunk["range_start"])
            + "::timestamptz) x; IF dropped IS DISTINCT FROM ARRAY["
            + literal(chunk["name"])
            + "]::text[] THEN RAISE EXCEPTION 'exact owned drop_chunks set mismatch'; END IF;END $guard$;",
        ]
    # Existing chunk bytes must survive the exact-owned row inverse unchanged.
    for chunk in before:
        body.append(
            f"DO $guard$ BEGIN IF NOT (SELECT count(*)={chunk['row_count']} AND encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\\n' ORDER BY to_jsonb(t)::text),''),'UTF8')),'hex')={literal(chunk['row_sha256'])} FROM {chunk['name']} t) THEN RAISE EXCEPTION 'preexisting chunk rowset changed';END IF;END $guard$;"
        )
    body += ["COMMIT;"]
    return "\n".join(body) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--witness", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists()
    args.output.write_text(render(json.loads(args.manifest.read_text()), json.loads(args.witness.read_text())))
    print(json.dumps({"rendered_only": True, "executed": False, "output": str(args.output)}))
