"""Synthetic source qualification only; no DB/device interaction."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from uuid import UUID

import pytest

SPEC = importlib.util.spec_from_file_location(
    "reconcile", Path(__file__).resolve().parents[1] / "scripts/experiment-v2-reconcile.py"
)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
ID = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"


def fixture():
    rows = []
    for i in range(2):
        identity = str(UUID(int=i + 1))
        day = f"2027-06-0{i + 1}"
        r = {
            "assignment_id": identity,
            "pair_index": 0,
            "local_date": day,
            "outcome_local_date": day,
            "blinded_arm": ["X", "Y"][i],
            "itt_start": day + "T12:00:00+00:00",
            "itt_end": f"2027-06-0{i + 2}T06:00:00+00:00",
            "delivery_failed": bool(i),
            "fallback_used": bool(i),
            "facility_rescue": bool(i),
            "zero_value_retained": True,
            "null_value_retained": bool(i),
            "exposure_seconds": 0,
            "expected_seconds": 64800,
            "outcome_frozen_at": "2027-06-04T00:00:00+00:00",
            "evidence_frozen_at": "2027-06-04T00:00:00+00:00",
            "integrity_passed": True,
        }
        r["outcome_preimage"] = json.dumps(
            {
                k: r[k]
                for k in (
                    "delivery_failed",
                    "fallback_used",
                    "facility_rescue",
                    "zero_value_retained",
                    "null_value_retained",
                )
            }
            | {"outcome": {"reset": bool(i), "value": None if i else 0}}
        )
        r["outcome_sha256"] = m.digest(
            b"verdify-experiment-v2-assigned-day-outcome-v1\0", r["outcome_preimage"], identity
        )
        for part in m.PARTS:
            payload = {"assignment_id": identity}
            if part == "integrity":
                payload.update(
                    {k: r[k] for k in ("outcome_sha256", "deviation_sha256", "fidelity_sha256", "environment_sha256")}
                )
            r[part + "_preimage"] = json.dumps(payload)
            r[part + "_sha256"] = m.digest(
                f"verdify-experiment-v2-{part}-v1".encode() + b"\0", r[part + "_preimage"], identity
            )
        r["evidence_bundle_sha256"] = hashlib.sha256(
            m.DAY_DOMAIN
            + UUID(identity).bytes
            + b"".join(bytes.fromhex(r[p + "_sha256"]) for p in ("outcome", *m.PARTS))
        ).hexdigest()
        rows.append(r)
    return {
        "experiment_id": ID,
        "as_of": "2027-06-04T01:00:00+00:00",
        "start": "2027-06-01",
        "pairs": 1,
        "design_lock_sha256": "a" * 64,
        "analyzer_environment_sha256": "b" * 64,
        "rows": rows,
        "export": None,
    }


def add_export(s):
    rows = []
    for i, r in enumerate(s["rows"], 1):
        x = {
            k: r[k]
            for k in (
                "assignment_id",
                "pair_index",
                "blinded_arm",
                "delivery_failed",
                "fallback_used",
                "facility_rescue",
                "zero_value_retained",
                "null_value_retained",
                "outcome_sha256",
                "evidence_bundle_sha256",
                *(p + "_sha256" for p in m.PARTS),
            )
        }
        x.update(
            day_index=i,
            assigned_local_date=r["local_date"],
            itt_range=f'["{r["itt_start"]}","{r["itt_end"]}")',
            outcome=json.loads(r["outcome_preimage"])["outcome"],
        )
        rows.append(x)
    bundle = m.digest(m.EVIDENCE_DOMAIN, "".join(r["evidence_bundle_sha256"] for r in rows))
    payload = {
        "experiment_id": ID,
        "rows": rows,
        "evidence_bundle_sha256": bundle,
        "analyzer_environment_sha256": "b" * 64,
    }
    raw = json.dumps(payload)
    s["export"] = {
        "raw": raw,
        "sha256": m.digest(m.SQL_EXPORT_DOMAIN, raw),
        "evidence_bundle_sha256": bundle,
        "analyzer_environment_sha256": "b" * 64,
    }
    return s


def test_retains_failed_reset_zero_null_and_zero_exposure():
    s = add_export(fixture())
    r = m.reconcile(s)
    assert r["observed_assignments"] == r["locked_assigned_day_denominator"] == 2
    assert r["export_verified"] and all(x["state"] == "frozen_verified" for x in r["rows"])
    assert r["rows"][1]["delivery_failed"] and r["rows"][1]["null_value_retained"]
    assert all(x["exposure_seconds"] == 0 for x in r["rows"])
    assert all("outcome" not in x for x in r["rows"])


def test_missing_outcome_stays_in_denominator():
    s = fixture()
    r = s["rows"][1]
    for k in list(r):
        if k not in {"assignment_id", "pair_index", "local_date"}:
            r[k] = None
    result = m.reconcile(s)
    assert result["observed_assignments"] == 2
    assert result["rows"][1]["state"] == "missing_outcome" and result["status"] == "incomplete"


def test_missing_assignment_does_not_shrink_calendar():
    s = fixture()
    s["rows"].pop()
    r = m.reconcile(s)
    assert r["locked_assigned_day_denominator"] == 2 and r["missing_calendar_days"] == ["2027-06-02"]


def test_draft_has_no_invented_assignments():
    s = fixture()
    s.update(rows=[], start=None, pairs=None, design_lock_sha256=None)
    r = m.reconcile(s)
    assert r["status"] == "not_design_locked" and r["locked_assigned_day_denominator"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "pair",
        "hash",
        "window",
        "export_drop",
        "export_value",
        "export_secret",
        "export_hash",
        "export_window",
    ],
)
def test_refuses_corruption(mutation):
    s = add_export(fixture())
    if mutation == "duplicate":
        s["rows"][1] = copy.deepcopy(s["rows"][0])
    elif mutation == "pair":
        s["rows"][0]["pair_index"] = 1
    elif mutation == "hash":
        s["rows"][0]["outcome_sha256"] = "c" * 64
    elif mutation == "window":
        s["rows"][0]["itt_start"] = "2027-06-01T11:00:00+00:00"
    else:
        p = json.loads(s["export"]["raw"])
        if mutation == "export_drop":
            p["rows"].pop()
        elif mutation == "export_value":
            p["rows"][0]["outcome"]["value"] = 5
        elif mutation == "export_secret":
            p["rows"][0]["secret"] = "forbidden"
        elif mutation == "export_window":
            p["rows"][0]["itt_range"] = '["2027-06-01T11:00:00Z","2027-06-02T06:00:00Z")'
        s["export"]["raw"] = json.dumps(p)
        s["export"]["sha256"] = (
            m.digest(m.SQL_EXPORT_DOMAIN, s["export"]["raw"]) if mutation != "export_hash" else "d" * 64
        )
    with pytest.raises(ValueError):
        m.reconcile(s)


def test_sql_is_one_read_only_whitelisted_select():
    q = m.query(ID)
    assert q.startswith("SELECT") and "control_assignments a" in q and "LEFT JOIN" in q
    assert not any(
        x in q.lower()
        for x in ["secret", "reveal", "randomization", "arm_label", "fn_experiment", "delete", "update", "insert"]
    )
    with pytest.raises(ValueError):
        m.query("'; DROP TABLE anything")


def test_receipt_schema_and_deterministic_replay():
    import jsonschema

    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "research/planner-efficacy/protocols/daily-reconciliation-v1.schema.json"
        ).read_text()
    )
    for snapshot in [fixture(), add_export(fixture())]:
        result = m.reconcile(snapshot)
        jsonschema.validate({"receipt": result, "sha256": "a" * 64}, schema)
        assert result == m.reconcile(copy.deepcopy(snapshot))


def test_retained_flags_cannot_disagree_with_hashed_source():
    snapshot = fixture()
    snapshot["rows"][0]["delivery_failed"] = True
    with pytest.raises(ValueError, match="flag lineage"):
        m.reconcile(snapshot)


def test_rehashed_permutation_with_renumbered_days_and_bundle_is_refused():
    snapshot = add_export(fixture())
    payload = json.loads(snapshot["export"]["raw"])
    payload["rows"].reverse()
    for index, row in enumerate(payload["rows"], 1):
        row["day_index"] = index
    bundle = m.digest(m.EVIDENCE_DOMAIN, "".join(r["evidence_bundle_sha256"] for r in payload["rows"]))
    payload["evidence_bundle_sha256"] = bundle
    snapshot["export"]["evidence_bundle_sha256"] = bundle
    snapshot["export"]["raw"] = json.dumps(payload)
    snapshot["export"]["sha256"] = m.digest(m.SQL_EXPORT_DOMAIN, snapshot["export"]["raw"])
    with pytest.raises(ValueError, match="chronological assignment order"):
        m.reconcile(snapshot)
    assert len(snapshot["rows"]) == 2  # structural refusal never filters source assignments


def test_future_scheduled_snapshot_is_reconciled_but_not_completed():
    snapshot = fixture()
    snapshot["as_of"] = "2027-06-01T11:00:00+00:00"
    for row in snapshot["rows"]:
        for key in list(row):
            if key not in {"assignment_id", "pair_index", "local_date"}:
                row[key] = None
    report = m.reconcile(snapshot)
    assert report["status"] == "reconciled" and not report["export_verified"]
    assert report["observed_assignments"] == 2 and all(r["state"] == "scheduled" for r in report["rows"])


def test_owning_regressions_are_in_existing_ci_group():
    runner = (Path(__file__).resolve().parents[1] / "scripts/ci-local.sh").read_text()
    assert "  tests/test_experiment_v2_reconcile.py \\\n" in runner
