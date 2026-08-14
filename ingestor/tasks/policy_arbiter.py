"""tasks.policy_arbiter — proposal arbiter for the controlled experiment
(#584 Lane C).

Runs every 60s. Feature-off (VERDIFY_POLICY_VECTOR_MODE=off or no
VERDIFY_ACTIVE_EXPERIMENT_ID) it returns immediately without touching the
database.

Each cycle it consumes eligible policy_proposals (state='proposed') for the
currently-open assignment and, per proposal:

  1. resolves the complete 49-field value set — proposed template components
     when the proposal is a template selection, otherwise the latest admitted
     vector (falling back to the frozen baseline template) overlaid with the
     proposal's explicit components;
  2. compiles the canonical Lane A wire artifact: quantize -> encode ->
     content_sha256 -> activation_sha256 with the assignment's §8.9
     treatment octets and the next device generation;
  3. completes the proposal's component rows to the full 49 (lineage-tagged
     'baseline' for carried-over fields) so admission's completeness gate is
     provable from the proposal itself;
  4. LIVE mode: calls fn_admit_policy_vector (migration 208) which enforces
     the arm/allowlist/template rules server-side and atomically persists
     vector + components + outbox row.
     SHADOW mode: transitions the proposal to state='shadow' carrying the
     compiled content hash — persisted + compiled but NEVER an outbox row
     (the SQL additionally guards shadow-never-outbox: only state='proposed'
     is admittable).

Rule violations reject the proposal (state='rejected') with a bounded
state_reason; the append-only proposal stream is the audit trail.
"""

import uuid as _uuid

import asyncpg

from verdify_schemas.experiment_config import (
    POLICY_VECTOR_MODE_LIVE,
    POLICY_VECTOR_MODE_OFF,
    active_experiment_id,
    policy_device_id,
    policy_vector_mode,
)
from verdify_schemas.policy_vector import (
    QUALIFICATION_OPERATION_CODES,
    TREATMENT_TAG_AA,
    TREATMENT_TAG_QUALIFICATION,
    TREATMENT_TAG_RANDOMIZED,
    WIRE_KIND_WIDTH,
    activation_sha256,
    content_sha256,
    encode_policy_vector,
    quantize_policy_values,
    wire_component_index,
    wire_fields,
)
from verdify_schemas.tunable_registry import WIRE_SCHEMA_VERSION

from ._common import json, log

_MAX_PROPOSALS_PER_CYCLE = 5
_STATE_REASON_MAX = 200


class ArbiterReject(ValueError):
    """A proposal violates an arbiter rule; carries the bounded reason."""


def _wire_defs() -> dict:
    return {defn.name: defn for defn in wire_fields()}


def _encoded_value_bytes(name: str, value: float | bool) -> bytes:
    """The raw big-endian wire value bytes for one quantized component."""
    defn = _wire_defs()[name]
    raw = int(round(float(value) * (defn.wire_scale or 1)))
    return raw.to_bytes(WIRE_KIND_WIDTH[defn.wire_kind or ""], "big", signed=defn.wire_kind == "i16")


def _aa_lane_octet(arm_label: str) -> int:
    """Map an A/A assignment arm label onto the §8.9 lane octet (0x00/0x01)."""
    label = (arm_label or "").strip().lower()
    for candidate in ("lane0", "lane1", "0", "1"):
        if label == candidate:
            return int(candidate[-1])
    raise ArbiterReject(f"aa_lane arm_label {arm_label!r} does not resolve to lane 0/1")


def treatment_bytes_for_assignment(assignment) -> bytes:
    """Build the §8.9 treatment octets for one assignment row."""
    kind = assignment["operation_kind"]
    label = assignment["arm_label"]
    if kind == "randomized_day":
        if label not in ("X", "Y"):
            raise ArbiterReject(f"randomized_day arm_label {label!r} must be blinded X/Y")
        return bytes([TREATMENT_TAG_RANDOMIZED, ord(label)])
    if kind == "aa_lane":
        return bytes([TREATMENT_TAG_AA, _aa_lane_octet(label)])
    operation_code = QUALIFICATION_OPERATION_CODES.get(kind)
    if operation_code is None:
        raise ArbiterReject(f"operation_kind {kind!r} has no treatment encoding")
    strata = assignment["frozen_strata"]
    if isinstance(strata, str):
        strata = json.loads(strata)
    strata = strata or {}
    try:
        source = _uuid.UUID(str(strata["source_template_id"]))
        target = _uuid.UUID(str(strata["target_template_id"]))
        regime = int(strata["regime"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ArbiterReject(
            f"qualification assignment frozen_strata must carry source/target template ids + regime ({exc})"
        ) from exc
    if not 0 <= regime <= 3:
        raise ArbiterReject(f"qualification regime {regime} not in 0..3")
    return bytes([TREATMENT_TAG_QUALIFICATION, operation_code]) + source.bytes + target.bytes + bytes([regime])


async def _template_values(conn, template_id) -> dict[str, float]:
    rows = await conn.fetch(
        "SELECT field_name, normalized_value FROM policy_template_components WHERE template_id = $1::uuid",
        template_id,
    )
    return {row["field_name"]: float(row["normalized_value"]) for row in rows}


async def _resolve_full_values(conn, exp, proposal) -> tuple[dict[str, float | bool], set[str]]:
    """The complete 49-field value set + the fields the proposal set explicitly."""
    explicit_rows = await conn.fetch(
        "SELECT field_name, normalized_value FROM policy_proposal_components WHERE proposal_id = $1::uuid",
        proposal["proposal_id"],
    )
    explicit = {row["field_name"]: float(row["normalized_value"]) for row in explicit_rows}

    base: dict[str, float] = {}
    if proposal["proposed_template_id"] is not None:
        base = await _template_values(conn, proposal["proposed_template_id"])
        if len(base) != 49:
            raise ArbiterReject("proposed template is incomplete (needs 49 components)")
    else:
        prior = await conn.fetchrow(
            """
            SELECT vector_id FROM effective_policy_vectors
             WHERE greenhouse_id = $1
             ORDER BY device_generation DESC
             LIMIT 1
            """,
            exp["greenhouse_id"],
        )
        if prior is not None:
            rows = await conn.fetch(
                "SELECT field_name, normalized_value FROM effective_policy_vector_components WHERE vector_id = $1::uuid",
                prior["vector_id"],
            )
            base = {row["field_name"]: float(row["normalized_value"]) for row in rows}
        if len(base) != 49:
            baseline = await conn.fetchrow(
                "SELECT template_id FROM policy_templates WHERE experiment_id = $1::uuid AND kind = 'baseline'",
                exp["experiment_id"],
            )
            if baseline is None:
                raise ArbiterReject("no base vector: neither a prior admitted vector nor a baseline template exists")
            base = await _template_values(conn, baseline["template_id"])
            if len(base) != 49:
                raise ArbiterReject("baseline template is incomplete (needs 49 components)")

    values: dict[str, float | bool] = {**base, **explicit}
    expected = {defn.name for defn in wire_fields()}
    missing = sorted(expected - set(values))
    extra = sorted(set(values) - expected)
    if missing or extra:
        raise ArbiterReject(f"resolved values are not the 49 wire fields: missing={missing[:5]} extra={extra[:5]}")
    return values, set(explicit)


async def _complete_proposal_components(
    conn,
    proposal,
    quantized: dict[str, float | bool],
    explicit_fields: set[str],
) -> None:
    """Insert the carried-over components so the proposal itself proves 49."""
    template_selected = proposal["proposed_template_id"] is not None
    for name, value in quantized.items():
        if name in explicit_fields:
            continue
        await conn.execute(
            """
            INSERT INTO policy_proposal_components
                (proposal_id, field_name, component_index, normalized_value, encoded_value, producer)
            VALUES ($1::uuid, $2, $3, $4, $5, $6)
            ON CONFLICT (proposal_id, field_name) DO NOTHING
            """,
            proposal["proposal_id"],
            name,
            wire_component_index(name),
            float(value),
            _encoded_value_bytes(name, value),
            # Template-selection fields carry the proposal's own producer
            # (COALESCEd at vector copy); carried-over base fields are
            # lineage-tagged as baseline continuation.
            None if template_selected else "baseline",
        )


async def _set_proposal_state(conn, proposal_id, state: str, reason: str) -> None:
    await conn.execute(
        "UPDATE policy_proposals SET state = $2, state_reason = $3, updated_at = now() WHERE proposal_id = $1::uuid",
        proposal_id,
        state,
        reason[:_STATE_REASON_MAX],
    )


def _validity_range(proposal, assignment):
    validity = proposal["validity"] if proposal["validity"] is not None else assignment["valid_range"]
    if validity is None or validity.lower is None or validity.upper is None:
        raise ArbiterReject("proposal/assignment validity must be a bounded range")
    return validity


async def policy_arbiter_admissions(pool: asyncpg.Pool) -> None:
    """Compile + admit (or shadow-compile) eligible proposals."""
    mode = policy_vector_mode()
    experiment_id = active_experiment_id()
    if mode == POLICY_VECTOR_MODE_OFF or not experiment_id:
        return

    async with pool.acquire() as conn:
        exp = await conn.fetchrow(
            """
            SELECT experiment_id::text AS experiment_id, status, kind, greenhouse_id,
                   schema_revision, manifest_revision, compiler_revision, registry_revision,
                   mutable_fields
              FROM control_experiments
             WHERE experiment_id = $1::uuid
            """,
            experiment_id,
        )
        if exp is None or exp["status"] not in ("armed", "running"):
            return

        assignment = await conn.fetchrow(
            """
            SELECT assignment_id::text AS assignment_id, arm_label, operation_kind,
                   frozen_strata, valid_range
              FROM control_assignments
             WHERE experiment_id = $1::uuid
               AND status = 'active'
               AND now() <@ valid_range
             ORDER BY lower(valid_range) DESC
             LIMIT 1
            """,
            experiment_id,
        )
        if assignment is None:
            return

        proposals = await conn.fetch(
            """
            SELECT proposal_id::text AS proposal_id, producer, proposed_template_id,
                   validity, trigger_ref
              FROM policy_proposals
             WHERE assignment_id = $1::uuid
               AND state = 'proposed'
             ORDER BY created_at
             LIMIT $2
            """,
            assignment["assignment_id"],
            _MAX_PROPOSALS_PER_CYCLE,
        )
        if not proposals:
            return

        revisions = {
            "schema": exp["schema_revision"],
            "manifest": exp["manifest_revision"],
            "compiler": exp["compiler_revision"],
            "registry": exp["registry_revision"],
        }
        device_id = policy_device_id(exp["greenhouse_id"])

        for proposal in proposals:
            try:
                if any(value is None for value in revisions.values()):
                    raise ArbiterReject("experiment revisions are not frozen (schema/manifest/compiler/registry)")
                values, explicit_fields = await _resolve_full_values(conn, exp, proposal)
                quantized = quantize_policy_values(values)
                vector_bytes = encode_policy_vector(quantized)
                content_hash = content_sha256(
                    vector_bytes,
                    schema_version=WIRE_SCHEMA_VERSION,
                    policy_revision_ids=revisions,
                )
                validity = _validity_range(proposal, assignment)
                treatment = treatment_bytes_for_assignment(assignment)
                next_generation = await conn.fetchval(
                    "SELECT COALESCE(max(device_generation), 0) + 1 FROM effective_policy_vectors WHERE greenhouse_id = $1",
                    exp["greenhouse_id"],
                )
                activation_hash = activation_sha256(
                    content_hash,
                    experiment_id=exp["experiment_id"],
                    assignment_id=assignment["assignment_id"],
                    treatment_bytes=treatment,
                    generation=int(next_generation),
                    valid_from_us=int(validity.lower.timestamp() * 1_000_000),
                    valid_to_us=int(validity.upper.timestamp() * 1_000_000),
                )
                await _complete_proposal_components(conn, proposal, quantized, explicit_fields)
            except (ArbiterReject, ValueError) as exc:
                await _set_proposal_state(conn, proposal["proposal_id"], "rejected", f"arbiter: {exc}")
                log.warning("policy_arbiter: rejected proposal %s: %s", proposal["proposal_id"], exc)
                continue

            if mode != POLICY_VECTOR_MODE_LIVE:
                # SHADOW: persisted + compiled, NEVER an outbox row. The SQL
                # admission path additionally refuses non-'proposed' states.
                await _set_proposal_state(
                    conn,
                    proposal["proposal_id"],
                    "shadow",
                    f"shadow-compiled content_sha256={content_hash.hex()}",
                )
                log.info(
                    "policy_arbiter: shadow-compiled proposal %s (content %s)",
                    proposal["proposal_id"],
                    content_hash.hex()[:12],
                )
                continue

            try:
                vector_id = await conn.fetchval(
                    "SELECT fn_admit_policy_vector($1::uuid, $2, $3::tstzrange, $4, $5::bytea, $6, $7, $8)",
                    proposal["proposal_id"],
                    device_id,
                    validity,
                    "policy_arbiter",
                    vector_bytes,
                    content_hash.hex(),
                    activation_hash.hex(),
                    int(next_generation),
                )
                log.info(
                    "policy_arbiter: admitted proposal %s as vector %s (generation %d, content %s)",
                    proposal["proposal_id"],
                    vector_id,
                    int(next_generation),
                    content_hash.hex()[:12],
                )
            except asyncpg.PostgresError as exc:
                await _set_proposal_state(conn, proposal["proposal_id"], "rejected", f"admission: {exc}")
                log.warning("policy_arbiter: admission rejected proposal %s: %s", proposal["proposal_id"], exc)
