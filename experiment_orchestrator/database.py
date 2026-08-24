"""Function-only asyncpg boundary and exact duty-role attestation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg

from .contracts import OrchestratorMode
from .settings import DatabaseSettings


class DatabaseContractError(RuntimeError):
    """The dedicated login cannot prove the required restricted boundary."""


class ConnectionLike(Protocol):
    async def fetchrow(self, query: str, *args: object) -> Mapping[str, object] | None: ...


class AcquireLike(Protocol):
    async def __aenter__(self) -> ConnectionLike: ...

    async def __aexit__(self, *args: object) -> bool | None: ...


class PoolLike(Protocol):
    def acquire(self) -> AcquireLike: ...

    async def close(self) -> None: ...


PoolFactory = Callable[..., Awaitable[PoolLike]]

# This is the complete v2 EXECUTE surface for each duty, not merely the calls
# made by this process.  A migration that adds/removes a grant intentionally
# makes startup fail until this attestation contract is reviewed in lockstep.
# The final cycle signatures are filled from migration 214's source-locked
# grant block; keep the centralized shape for tests and deployment probes.
ROLE_FUNCTIONS: dict[OrchestratorMode, tuple[str, ...]] = {
    OrchestratorMode.LIFECYCLE: (
        "public.fn_experiment_v2_schedule_shadow_cycle(uuid,date,timestamp with time zone,text,text,text,text,text,text)",
        "public.fn_experiment_v2_due_shadow_cycle(uuid)",
        "public.fn_experiment_v2_due_assignment(uuid)",
        "public.fn_experiment_v2_boundary_cycle(uuid,text)",
    ),
    OrchestratorMode.SELECTOR: (
        "public.fn_experiment_v2_finalize_randomization(uuid,text)",
        "public.fn_experiment_v2_selector_cycle(uuid)",
        "public.fn_experiment_v2_record_selector_choice(uuid,uuid,text,text,text,text,text,text,text[],text,text)",
        "public.fn_experiment_v2_record_shadow_choice(uuid,uuid,text,text,text,text,text,text,text[],text,text)",
        "public.fn_experiment_v2_reveal(uuid,text)",
    ),
    OrchestratorMode.FREEZER: (
        "public.fn_experiment_v2_outcome_source_cycle(uuid)",
        "public.fn_experiment_v2_freeze_outcome(uuid,uuid,jsonb,boolean,boolean,boolean,boolean,boolean,text)",
        "public.fn_experiment_v2_freeze_day_evidence(uuid,uuid,jsonb,jsonb,jsonb,text)",
        "public.fn_experiment_v2_freeze_export(uuid,text,text)",
        "public.fn_experiment_v2_record_shadow_outcome_preview(uuid,uuid,jsonb,text)",
    ),
}

ROLE_NAMES: dict[OrchestratorMode, str] = {
    OrchestratorMode.LIFECYCLE: "verdify_experiment_shadow_scheduler",
    OrchestratorMode.SELECTOR: "verdify_experiment_randomizer",
    OrchestratorMode.FREEZER: "verdify_experiment_outcome_freezer",
}

ROLE_ATTESTATION_SQL = """
WITH login AS (
    SELECT oid, rolname, rolcanlogin, rolinherit, rolsuper, rolcreatedb,
           rolcreaterole, rolreplication, rolbypassrls
      FROM pg_roles
     WHERE rolname = current_user
), duty AS (
    SELECT oid, rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,
           rolreplication, rolbypassrls
      FROM pg_roles
     WHERE rolname = $1::text
), allowed_functions(function_signature) AS (
    SELECT unnest($2::text[])
)
SELECT current_user::text AS current_user_name,
       session_user::text AS session_user_name,
       current_user = session_user AS session_user_matches,
       pg_has_role(current_user, $1::text, 'member') AS duty_member,
       coalesce((
           SELECT NOT membership.admin_option
             FROM pg_auth_members membership
             CROSS JOIN login CROSS JOIN duty
            WHERE membership.member = login.oid
              AND membership.roleid = duty.oid
       ), false) AS duty_membership_non_admin,
       coalesce((SELECT rolcanlogin AND rolinherit FROM login), false) AS login_role_safe,
       coalesce((SELECT rolsuper FROM login), true) AS is_superuser,
       coalesce((
           SELECT database_row.datdba = login.oid
             FROM pg_database database_row CROSS JOIN login
            WHERE database_row.datname = current_database()
       ), true) AS is_database_owner,
       coalesce((
           SELECT rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
             FROM login
       ), true) AS has_elevated_role_attributes,
       coalesce((
           SELECT NOT duty.rolcanlogin AND NOT duty.rolinherit AND NOT duty.rolsuper AND
                  NOT duty.rolcreatedb AND NOT duty.rolcreaterole AND
                  NOT duty.rolreplication AND NOT duty.rolbypassrls AND
                  NOT EXISTS (
                      SELECT 1
                        FROM pg_roles inherited
                       WHERE inherited.oid <> duty.oid
                         AND pg_has_role(duty.oid, inherited.oid, 'member')
                  )
             FROM duty
       ), false) AS duty_role_safe,
       EXISTS (
           SELECT 1
             FROM pg_roles inherited
            WHERE inherited.rolname NOT IN (current_user, $1::text)
              AND pg_has_role(current_user, inherited.oid, 'member')
       ) AS has_other_role_membership,
       EXISTS (
           SELECT 1
             FROM pg_namespace namespace CROSS JOIN login
            WHERE namespace.nspname = 'public' AND namespace.nspowner = login.oid
           UNION ALL
           SELECT 1
             FROM pg_class owned
             JOIN pg_namespace namespace ON namespace.oid = owned.relnamespace
             CROSS JOIN login
            WHERE namespace.nspname = 'public' AND owned.relowner = login.oid
           UNION ALL
           SELECT 1
             FROM pg_proc owned
            JOIN pg_namespace namespace ON namespace.oid = owned.pronamespace
             CROSS JOIN login
            WHERE namespace.nspname = 'public' AND owned.proowner = login.oid
       ) AS has_managed_object_ownership,
       has_schema_privilege(current_user, 'public', 'CREATE') AS has_public_schema_create,
       EXISTS (
           SELECT 1
             FROM pg_class protected
             JOIN pg_namespace namespace ON namespace.oid = protected.relnamespace
            WHERE namespace.nspname = 'public'
              AND protected.relkind IN ('r', 'p', 'v', 'm', 'f')
              -- Function-only logins may not read or mutate *any* public
              -- relation. This necessarily covers equipment_counter_samples,
              -- equipment_direct_state_snapshots,
              -- equipment_state_source_receipts, all v2 tables, legacy
              -- policy/outbox/setpoint actuation tables, and public views.
              AND (
                  has_table_privilege(
                      current_user, protected.oid,
                      'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
                  )
                  OR has_any_column_privilege(
                      current_user, protected.oid,
                      'SELECT,INSERT,UPDATE,REFERENCES'
                  )
              )
       ) AS has_protected_relation_privilege,
       EXISTS (
           SELECT 1
             FROM pg_class protected
             JOIN pg_namespace namespace ON namespace.oid = protected.relnamespace
            WHERE namespace.nspname = 'public'
              AND protected.relkind = 'S'
              AND has_sequence_privilege(current_user, protected.oid, 'USAGE,SELECT,UPDATE')
       ) AS has_protected_sequence_privilege,
       EXISTS (
           SELECT 1
             FROM pg_proc candidate
             JOIN pg_namespace namespace ON namespace.oid = candidate.pronamespace
            WHERE namespace.nspname = 'public'
              AND (candidate.proname LIKE 'fn_experiment_v2_%' OR candidate.prosecdef)
              AND has_function_privilege(current_user, candidate.oid, 'EXECUTE')
              AND NOT EXISTS (
                  SELECT 1
                    FROM allowed_functions allowed
                   WHERE to_regprocedure(allowed.function_signature) = candidate.oid
              )
       ) AS has_unexpected_function_execute,
       NOT EXISTS (
           SELECT 1
             FROM allowed_functions required
            WHERE to_regprocedure(required.function_signature) IS NULL
               OR NOT has_function_privilege(
                   current_user, to_regprocedure(required.function_signature), 'EXECUTE'
               )
       ) AS has_required_function_execute
"""


def role_attestation_passes(row: Mapping[str, object] | None, expected_user: str) -> bool:
    if row is None:
        return False
    try:
        return bool(
            row["current_user_name"] == expected_user
            and row["session_user_name"] == expected_user
            and row["session_user_matches"] is True
            and row["duty_member"] is True
            and row["duty_membership_non_admin"] is True
            and row["login_role_safe"] is True
            and row["is_superuser"] is False
            and row["is_database_owner"] is False
            and row["has_elevated_role_attributes"] is False
            and row["duty_role_safe"] is True
            and row["has_other_role_membership"] is False
            and row["has_managed_object_ownership"] is False
            and row["has_public_schema_create"] is False
            and row["has_protected_relation_privilege"] is False
            and row["has_protected_sequence_privilege"] is False
            and row["has_unexpected_function_execute"] is False
            and row["has_required_function_execute"] is True
        )
    except (KeyError, TypeError):
        return False


@dataclass
class AttestedPool:
    """A pool that exists only after one exact login/role/function proof."""

    pool: PoolLike
    mode: OrchestratorMode

    def acquire(self) -> AcquireLike:
        return self.pool.acquire()

    async def close(self) -> None:
        await self.pool.close()


async def create_attested_pool(
    settings: DatabaseSettings,
    mode: OrchestratorMode,
    *,
    pool_factory: PoolFactory = asyncpg.create_pool,
) -> AttestedPool:
    allowed_functions = ROLE_FUNCTIONS[mode]
    if settings.duty_role != ROLE_NAMES[mode]:
        raise DatabaseContractError("database duty role does not match selected mode")
    if not allowed_functions:
        raise DatabaseContractError("database function contract has not been source-locked")
    candidate: PoolLike | None = None
    try:
        candidate = await pool_factory(
            host=settings.host,
            port=settings.port,
            database=settings.database,
            user=settings.user,
            password=settings.password,
            min_size=1,
            max_size=1,
            command_timeout=settings.statement_timeout_ms / 1000,
            max_inactive_connection_lifetime=60,
            server_settings={
                "application_name": f"verdify-experiment-v2-{mode.value}",
                "statement_timeout": str(settings.statement_timeout_ms),
            },
        )
        async with candidate.acquire() as connection:
            attestation = await connection.fetchrow(
                ROLE_ATTESTATION_SQL,
                settings.duty_role,
                list(allowed_functions),
            )
        if not role_attestation_passes(attestation, settings.user):
            raise DatabaseContractError("dedicated database login failed exact duty attestation")
        return AttestedPool(candidate, mode)
    except Exception:
        if candidate is not None:
            try:
                await candidate.close()
            except Exception:
                pass
        raise


def record_to_mapping(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Detach an asyncpg Record without broadening the database surface."""

    return None if row is None else dict(row)
