"""Contained clone-only invocation of the monitor's real component alert path.

The caller supplies an already authenticated ordinary-role connection, an
explicitly authorized sender, and the current-function fixture resolution.
No credentials, networking policy, lifecycle authority, or hardware are created.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

EXPERIMENT_ID = "cac56395-87b6-431d-b559-71e463987839"
SENSOR_ID = f"experiment.v2.{EXPERIMENT_ID}"
GREENHOUSE = "synthetic-s3-639-587-20261018"
CHANNEL = "C0ANVVAPLD6"
ROLE = "verdify_ingestor_runtime_login"
LABEL = "[NONPRODUCTION SYNTHETIC QUALIFICATION] #587 retained clone: "


def _require(value, reason):
    if not value:
        raise ValueError(reason)


async def _qualify_component_alert_lifecycle(
    conn,
    admission,
    *,
    post_message,
    resolve_fixture,
    record_receipt,
):
    """Fire, deduplicate, resolve and roll back one synthetic alert row.

    ``resolve_fixture`` must commit only the admitted synthetic shadow-work
    cancellation through the current executor LOGIN function. Its separate
    transaction is intentionally retained as synthetic evidence. The caller's
    native before/after seals account for that work/event and all sequences.
    ``post_message`` uses an existing, separately authorized production sender;
    its native request/result custody must correlate with emitted SHA-256/ts.
    This function never obtains that sender's credential or calls it implicitly.
    """
    from .alerts import component_experiment_alert_cycle

    _require(admission.get("experiment_id") == EXPERIMENT_ID, "wrong_experiment")
    _require(admission.get("greenhouse_id") == GREENHOUSE, "wrong_greenhouse")
    _require(admission.get("namespace") == "verdify-db-rehearsal", "wrong_namespace")
    _require(admission.get("cluster") == "verdify-cnpg-s2-integration", "wrong_cluster")
    _require(admission.get("database") == "verdify_rehearsal", "wrong_database")
    _require(admission.get("channel") == CHANNEL, "wrong_channel")
    _require(admission.get("human_message_authorization"), "message_authorization_missing")
    _require(not conn.is_in_transaction(), "unexpected_existing_transaction")
    _require(callable(post_message) and callable(resolve_fixture), "missing_explicit_adapter")
    _require(callable(record_receipt), "durable_receipt_sink_required")
    identity = dict(
        await conn.fetchrow(
            "SELECT current_user::text AS current_user, session_user::text AS session_user, "
            "current_database() AS database, current_setting('cluster_name') AS cluster, "
            "inet_server_addr()::text AS server_address, inet_server_port() AS server_port, "
            "pg_postmaster_start_time()::text AS postmaster_start, "
            "(SELECT oid::int FROM pg_database WHERE datname=current_database()) AS database_oid"
        )
    )
    _require(identity["current_user"] == identity["session_user"] == ROLE, "actual_login_mismatch")
    for key in ("database", "cluster", "server_address", "postmaster_start", "database_oid"):
        _require(identity[key] == admission.get(key), "native_target_mismatch:" + key)
    _require(identity["server_port"] == 5432, "native_port_mismatch")
    record_receipt(
        {
            "phase": "identity",
            "identity": identity,
            "primary_uid": admission["primary_uid"],
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
    )

    deliveries = []

    def bounded_send(channel, text, *, thread_ts=None):
        _require(channel == CHANNEL and text.startswith(LABEL), "delivery_scope_mismatch")
        _require(len(text.encode()) <= 8192 and EXPERIMENT_ID in text, "delivery_payload_mismatch")
        _require(len(deliveries) < 2, "delivery_budget_exhausted")
        if not deliveries:
            _require(thread_ts is None and "*[CRITICAL]*" in text, "expected_critical_fire")
        else:
            _require(thread_ts == deliveries[0]["slack_ts"] and "Resolved:" in text, "expected_same_thread_resolution")
        payload = {"channel": channel, "text": text, "thread_ts": thread_ts}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        record_receipt(
            {"phase": "delivery_attempt", "ordinal": len(deliveries) + 1, "request_sha256": digest, "payload": payload}
        )
        slack_ts = post_message(channel, text, thread_ts=thread_ts)
        _require(isinstance(slack_ts, str) and re.fullmatch(r"\d+\.\d+", slack_ts), "native_sender_receipt_missing")
        receipt = {
            "phase": "delivery_confirmed",
            "request_sha256": digest,
            "channel": channel,
            "thread_ts": thread_ts,
            "slack_ts": slack_ts,
        }
        deliveries.append(receipt)
        record_receipt(receipt)
        return slack_ts

    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(
            "SET LOCAL statement_timeout='15000'; SET LOCAL lock_timeout='1000'; "
            "SET LOCAL idle_in_transaction_session_timeout='90000'"
        )
        count = await conn.fetchval(
            "SELECT count(*) FROM (SELECT 1 FROM alert_log WHERE alert_type=$1 AND sensor_id=$2 LIMIT 2) q",
            "component_experiment_integrity",
            SENSOR_ID,
        )
        _require(count == 0, "synthetic_alert_already_exists")
        reason = await conn.fetchval(
            "SELECT alert_reason FROM public.fn_experiment_v2_ops_status() WHERE experiment_id=$1::uuid",
            EXPERIMENT_ID,
        )
        _require(reason == "expired_work_not_terminal", "expected_native_expired_shadow_work")
        first = await component_experiment_alert_cycle(
            conn,
            EXPERIMENT_ID,
            greenhouse_id=GREENHOUSE,
            post_message=bounded_send,
            message_prefix=LABEL,
        )
        _require(
            first == {"new": 1, "escalated": 0, "resolved": 0, "suppressed": 0} and len(deliveries) == 1,
            "fire_not_confirmed",
        )
        duplicate = await component_experiment_alert_cycle(
            conn,
            EXPERIMENT_ID,
            greenhouse_id=GREENHOUSE,
            post_message=bounded_send,
            message_prefix=LABEL,
        )
        _require(not any(duplicate.values()) and len(deliveries) == 1, "deduplication_failed")
        transition = await resolve_fixture()
        record_receipt({"phase": "fixture_resolution", "native_transition": transition})
        resolved = await component_experiment_alert_cycle(
            conn,
            EXPERIMENT_ID,
            greenhouse_id=GREENHOUSE,
            post_message=bounded_send,
            message_prefix=LABEL,
        )
        _require(
            resolved == {"new": 0, "escalated": 0, "resolved": 1, "suppressed": 0} and len(deliveries) == 2,
            "resolution_not_confirmed",
        )
    finally:
        await tx.rollback()
        record_receipt({"phase": "alert_transaction_rolled_back"})
    count = await conn.fetchval(
        "SELECT count(*) FROM (SELECT 1 FROM alert_log WHERE alert_type=$1 AND sensor_id=$2 LIMIT 2) q",
        "component_experiment_integrity",
        SENSOR_ID,
    )
    _require(count == 0, "synthetic_alert_rollback_failed")
    result = {
        "phase": "complete",
        "experiment_id": EXPERIMENT_ID,
        "native_role": ROLE,
        "delivery_count": 2,
        "deduplication_passed": True,
        "alert_row_rolled_back": True,
        "physical_acceptance": False,
        "sequence_effects_require_native_custody": True,
        "deliveries": deliveries,
    }
    record_receipt(result)
    return result


async def qualify_component_alert_lifecycle(
    conn,
    admission,
    *,
    post_message,
    resolve_fixture,
    record_receipt,
):
    """Execute the finite path with a three-minute overall async deadline.

    The supplied synchronous sender must retain the production sender's bounded
    HTTP timeout (10 seconds); caller infrastructure must impose its own outer
    deadline and preserve failures, including an uncertain delivery outcome.
    """
    try:
        async with asyncio.timeout(180):
            return await _qualify_component_alert_lifecycle(
                conn,
                admission,
                post_message=post_message,
                resolve_fixture=resolve_fixture,
                record_receipt=record_receipt,
            )
    except BaseException as error:
        record_receipt({"phase": "failed", "error_type": type(error).__name__, "acceptance_claimed": False})
        raise
