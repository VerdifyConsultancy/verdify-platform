#!/usr/bin/env python3
"""Read-only existing-LOGIN attestation; run inside the deployed credential consumer.

Input is source-composed nonsecret SQL/duty metadata. Credentials stay in the
existing process environment, never SQL, argv, output or an exception traceback.
No experiment function, device operation, mutation or message is invoked.
"""

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime

import asyncpg

ROLE_ENV = {
    "lifecycle": "VERDIFY_EXPERIMENT_LIFECYCLE",
    "executor": "VERDIFY_EXPERIMENT_COMPONENT",
    "scheduler": "VERDIFY_EXPERIMENT_SHADOW_SCHEDULER",
    "randomizer": "VERDIFY_EXPERIMENT_RANDOMIZER",
    "freezer": "VERDIFY_EXPERIMENT_OUTCOME_FREEZER",
}


async def probe(request):
    role = request["role"]
    prefix = ROLE_ENV[role]
    username = os.environ.get(prefix + "_DB_USER", "")
    password = os.environ.get(prefix + "_DB_PASSWORD", "")
    result = {
        "schema": "verdify.runtime-login-readonly/1",
        "role": role,
        "observed_at": datetime.now(UTC).isoformat(),
        "probe_sha256": hashlib.sha256(open(__file__, "rb").read()).hexdigest() if __file__ != "<stdin>" else None,
        "attestation_sha256": hashlib.sha256(request["sql"].encode()).hexdigest(),
        "qualified": False,
        "experiment_functions_called": 0,
        "device_connections_opened": 0,
    }
    connection = None
    try:
        if username != request["login"] or not password:
            raise ValueError("credential_identity_missing_or_wrong")
        invalid = None
        try:
            invalid = await asyncpg.connect(
                host=os.environ["DB_HOST"],
                port=int(os.environ.get("DB_PORT", "5432")),
                database=os.environ.get("DB_NAME", "verdify"),
                user=username,
                password="deliberately-invalid-587639-auth-probe",  # noqa: S106 - intentionally rejected canary
                timeout=5,
                command_timeout=3,
            )
        except asyncpg.InvalidPasswordError:
            result["invalid_password_rejected"] = True
        else:
            if invalid is not None:
                await invalid.close(timeout=3)
            raise ValueError("invalid_password_was_not_rejected")
        connection = await asyncpg.connect(
            host=os.environ["DB_HOST"],
            port=int(os.environ.get("DB_PORT", "5432")),
            database=os.environ.get("DB_NAME", "verdify"),
            user=username,
            password=password,
            timeout=5,
            command_timeout=3,
            server_settings={"application_name": "verdify-587-639-readonly", "statement_timeout": "2000"},
        )
        async with connection.transaction(isolation="repeatable_read", readonly=True):
            await connection.execute("SET LOCAL lock_timeout='500ms'; SET LOCAL max_parallel_workers_per_gather=0")
            row = await connection.fetchrow(request["sql"], request["duty"], request["functions"])
            result["attestation"] = dict(row)
            result["identity"] = dict(
                await connection.fetchrow(
                    "SELECT current_user::text AS current_user, session_user::text AS session_user, "
                    "current_database() AS database, inet_server_addr()::text AS server_address, "
                    "inet_server_port() AS server_port, pg_postmaster_start_time()::text AS postmaster_start, "
                    "current_setting('transaction_read_only') AS transaction_read_only"
                )
            )
            true_fields = {
                "session_user_matches",
                "duty_member",
                "duty_membership_non_admin",
                "login_role_safe",
                "duty_role_safe",
                "has_required_function_execute",
            }
            false_fields = {
                "is_superuser",
                "is_database_owner",
                "has_elevated_role_attributes",
                "has_other_role_membership",
                "has_managed_object_ownership",
                "has_public_schema_create",
                "has_protected_relation_privilege",
                "has_protected_sequence_privilege",
                "has_unexpected_function_execute",
            }
            result["qualified"] = (
                row["current_user_name"] == row["session_user_name"] == username
                and all(row[k] is True for k in true_fields)
                and all(row[k] is False for k in false_fields)
                and result["identity"]["transaction_read_only"] == "on"
            )
    except Exception as error:
        result["failure_type"] = type(error).__name__
    finally:
        if connection is not None:
            await connection.close(timeout=3)
    return result


if __name__ == "__main__":
    request = json.loads(sys.argv[1])
    result = asyncio.run(probe(request))
    print(json.dumps(result, sort_keys=True))
    sys.exit(0 if result["qualified"] else 1)
