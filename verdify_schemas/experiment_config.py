"""Controlled-experiment feature flags + writer-demotion gate (Lane C, #584).

One source of truth for the three rollout flags Lane F ships in the prod
ConfigMap, readable identically from the ingestor, the MCP server, and the
forecast-action engine. Every reader tolerates the flags being absent (the
safe defaults ARE current production behavior):

    VERDIFY_POLICY_VECTOR_MODE                  off | shadow | live (default off;
                                                any unrecognized value fails safe
                                                to off — no new actuation path)
    VERDIFY_COMPONENT_EXPERIMENT_ENABLED        off | enabled (default off;
                                                v2 component work is admissible
                                                only while the generalized vector
                                                mode is explicitly ``off``)
    VERDIFY_ACTIVE_EXPERIMENT_ID                UUID of the live experiment
                                                (default unset)
    VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED "1" (default) keeps today's
                                                direct setpoint_plan writers;
                                                exactly "0" demotes them to
                                                policy-proposal producers

The demotion gate helpers here are duck-typed over an asyncpg-style
connection (fetchrow/fetchval) so this module stays import-light — no DB
driver dependency at import time.
"""

from __future__ import annotations

import json
import logging
import os
import uuid

log = logging.getLogger("experiment_config")

POLICY_VECTOR_MODE_ENV = "VERDIFY_POLICY_VECTOR_MODE"
COMPONENT_EXPERIMENT_ENABLED_ENV = "VERDIFY_COMPONENT_EXPERIMENT_ENABLED"
ACTIVE_EXPERIMENT_ID_ENV = "VERDIFY_ACTIVE_EXPERIMENT_ID"
LEGACY_DIRECT_POLICY_WRITES_ENV = "VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED"
POLICY_DEVICE_ID_ENV = "VERDIFY_POLICY_DEVICE_ID"

POLICY_VECTOR_MODE_OFF = "off"
POLICY_VECTOR_MODE_SHADOW = "shadow"
POLICY_VECTOR_MODE_LIVE = "live"
POLICY_VECTOR_MODES = (POLICY_VECTOR_MODE_OFF, POLICY_VECTOR_MODE_SHADOW, POLICY_VECTOR_MODE_LIVE)

COMPONENT_EXPERIMENT_OFF = "off"
COMPONENT_EXPERIMENT_ENABLED = "enabled"
COMPONENT_EXPERIMENT_MODES = (COMPONENT_EXPERIMENT_OFF, COMPONENT_EXPERIMENT_ENABLED)

_UNRECOGNIZED_MODE_LOGGED: set[str] = set()


def policy_vector_mode() -> str:
    """Resolve the policy-vector mode; unrecognized values fail safe to off.

    "off" is the safe direction for THIS flag: it admits no new actuation
    path and keeps the ingestor byte-identical to pre-experiment behavior.
    (The legacy-writer flag below fails safe in the opposite direction, to
    "enabled", for the same reason: a typo must preserve current prod.)
    """
    raw = os.environ.get(POLICY_VECTOR_MODE_ENV, "").strip().lower()
    if not raw:
        return POLICY_VECTOR_MODE_OFF
    if raw not in POLICY_VECTOR_MODES:
        if raw not in _UNRECOGNIZED_MODE_LOGGED:
            _UNRECOGNIZED_MODE_LOGGED.add(raw)
            log.warning("%s=%r unrecognized; failing safe to 'off'", POLICY_VECTOR_MODE_ENV, raw)
        return POLICY_VECTOR_MODE_OFF
    return raw


def component_experiment_mode() -> str:
    """Return the coarse v2 component capability mode.

    The only enabling spelling is exactly ``enabled`` after whitespace/case
    normalization. Missing and unrecognized values fail closed to ``off``.
    Database phase/admission remains the sole source of shadow versus physical
    authority; this flag deliberately has no shadow/live submodes.
    """
    raw = os.environ.get(COMPONENT_EXPERIMENT_ENABLED_ENV, "").strip().lower()
    if not raw:
        return COMPONENT_EXPERIMENT_OFF
    if raw not in COMPONENT_EXPERIMENT_MODES:
        key = f"{COMPONENT_EXPERIMENT_ENABLED_ENV}:{raw}"
        if key not in _UNRECOGNIZED_MODE_LOGGED:
            _UNRECOGNIZED_MODE_LOGGED.add(key)
            log.warning(
                "%s=%r unrecognized; failing safe to 'off'",
                COMPONENT_EXPERIMENT_ENABLED_ENV,
                raw,
            )
        return COMPONENT_EXPERIMENT_OFF
    return raw


def component_experiment_gate() -> tuple[bool, str]:
    """Resolve the environment-only v2 admission gate and audit reason.

    This helper intentionally inspects the generalized flag's *raw* value.
    ``policy_vector_mode()`` maps typos to ``off`` for legacy safety, but the
    component fast path requires the configured value to be explicitly and
    exactly ``off``. When enabled against any other value it hard-fails before
    a database query, work claim, outbox lease, or device call.
    """
    if component_experiment_mode() != COMPONENT_EXPERIMENT_ENABLED:
        return False, "component_capability_off"
    raw_vector_mode = os.environ.get(POLICY_VECTOR_MODE_ENV, "").strip().lower()
    if raw_vector_mode != POLICY_VECTOR_MODE_OFF:
        return False, "generalized_vector_mode_not_exactly_off"
    if active_experiment_id() is None:
        return False, "active_experiment_id_missing_or_invalid"
    return True, "admissible"


def component_experiment_enabled() -> bool:
    """True only for a fully admissible coarse component configuration."""
    return component_experiment_gate()[0]


def active_experiment_id() -> str | None:
    """The configured live experiment UUID, or None when unset/invalid."""
    raw = os.environ.get(ACTIVE_EXPERIMENT_ID_ENV, "").strip()
    if not raw:
        return None
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        if raw not in _UNRECOGNIZED_MODE_LOGGED:
            _UNRECOGNIZED_MODE_LOGGED.add(raw)
            log.warning("%s is not a valid UUID; experiment features stay off", ACTIVE_EXPERIMENT_ID_ENV)
        return None


def experiment_enabled() -> bool:
    """True only when mode != off AND an experiment id is configured."""
    return policy_vector_mode() != POLICY_VECTOR_MODE_OFF and active_experiment_id() is not None


def legacy_direct_policy_writes_enabled() -> bool:
    """True (current behavior) unless the flag is exactly "0".

    Any other value — absent, "1", or a typo — keeps today's direct
    setpoint_plan writers untouched; only a deliberate "0" demotes them.
    """
    return os.environ.get(LEGACY_DIRECT_POLICY_WRITES_ENV, "1").strip() != "0"


def policy_device_id(greenhouse_id: str) -> str:
    """The outbox/exposure device id for one greenhouse's ESP32."""
    return os.environ.get(POLICY_DEVICE_ID_ENV, "").strip() or f"esp32-{greenhouse_id}"


# ── Writer-demotion gate (MCP set_plan/set_tunable + forecast engine) ────────

_ARMED_ASSIGNMENT_SQL = """
SELECT e.experiment_id::text AS experiment_id,
       e.greenhouse_id,
       a.assignment_id::text AS assignment_id
  FROM public.control_experiments e
  LEFT JOIN public.control_assignments a
    ON a.experiment_id = e.experiment_id
   AND a.status = 'active'
   AND now() <@ a.valid_range
 WHERE e.status IN ('armed', 'running')
 ORDER BY e.armed_at DESC NULLS LAST
 LIMIT 1
"""


async def demoted_policy_write_gate(conn) -> dict | None:
    """Return demotion context when direct policy writes must become proposals.

    None => current behavior (write setpoint_plan directly). A dict =>
    demoted; it carries experiment_id/assignment_id (either may be None when
    legacy writes are disabled but no assignment is armed — the caller then
    reports "not recordable" instead of silently actuating).

    Fast path first: with legacy writes enabled and the experiment env flags
    off, NO database query runs — feature-off is byte-identical to today.
    """
    env_demoted = not legacy_direct_policy_writes_enabled()
    if not env_demoted and not experiment_enabled():
        return None
    try:
        row = await conn.fetchrow(_ARMED_ASSIGNMENT_SQL)
    except Exception as exc:  # migration 207 absent, transient DB error, ...
        if not env_demoted:
            # Armed-assignment probe only; fail open to current behavior.
            log.warning("experiment write-gate probe failed (%s); direct write proceeds", type(exc).__name__)
            return None
        return {"experiment_id": None, "assignment_id": None, "error": type(exc).__name__}
    if row is None or row["assignment_id"] is None:
        if env_demoted:
            return {
                "experiment_id": row["experiment_id"] if row else None,
                "assignment_id": None,
            }
        return None
    return {
        "experiment_id": row["experiment_id"],
        "assignment_id": row["assignment_id"],
        "greenhouse_id": row["greenhouse_id"],
    }


async def submit_policy_proposal(
    conn,
    *,
    producer: str,
    trigger_ref: str | None,
    components: list[dict] | None = None,
    proposed_template_id: str | None = None,
    digest_sha256: str | None = None,
    context: dict | None = None,
    state: str = "proposed",
    actor: str = "lane-c-writer",
    experiment_id: str | None = None,
    assignment_id: str | None = None,
) -> str:
    """Record one policy proposal through fn_submit_policy_proposal (mig 208)."""
    proposal_id = await conn.fetchval(
        "SELECT public.fn_submit_policy_proposal("
        "p_producer => $1, p_trigger_ref => $2, p_proposed_template_id => $3::uuid, "
        "p_components => $4::jsonb, p_digest_sha256 => $5, p_context => $6::jsonb, "
        "p_state => $7, p_actor => $8, p_experiment_id => $9::uuid, p_assignment_id => $10::uuid)",
        producer,
        trigger_ref,
        proposed_template_id,
        json.dumps(components) if components is not None else None,
        digest_sha256,
        json.dumps(context, sort_keys=True, default=str) if context is not None else None,
        state,
        actor,
        experiment_id,
        assignment_id,
    )
    return str(proposal_id)
