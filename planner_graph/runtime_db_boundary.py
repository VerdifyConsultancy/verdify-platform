"""Verify migration-owned planner schema and its dedicated runtime identity."""

from __future__ import annotations

import os
from collections.abc import Mapping

RUNTIME_LOGIN = "verdify_planner_runtime_login"
RUNTIME_DUTY = "verdify_planner_runtime"

SCHEMA_COLUMNS = {
    "planner_graph_runs": {
        "trigger_id",
        "thread_id",
        "status",
        "run_mode",
        "current_step",
        "terminal_status",
        "execution_owner",
        "last_error",
        "updated_at",
        "queued",
        "submission_count",
        "state",
        "lease_owner",
        "lease_expires_at",
        "started_at",
        "completed_at",
    },
    "planner_memory_items": {
        "memory_id",
        "greenhouse_id",
        "memory_type",
        "source_type",
        "source_id",
        "trigger_id",
        "event_type",
        "title",
        "summary",
        "body",
        "tags",
        "importance",
        "confidence",
        "trust_level",
        "content_hash",
        "payload",
        "is_active",
        "valid_from",
        "expires_at",
        "last_used_at",
        "created_at",
        "updated_at",
    },
    "planner_memory_retrievals": {
        "retrieval_id",
        "trigger_id",
        "greenhouse_id",
        "strategy",
        "query_text",
        "filters",
        "result_ids",
        "scores",
        "latency_ms",
        "created_at",
    },
}


def _value(row, name, index=0):
    return row[name] if isinstance(row, Mapping) else row[index]


def guard_connection(conn):
    """Reject owner/cross-workload identities; never include a DSN in errors."""
    if os.environ.get("VERDIFY_PLANNER_RUNTIME_DB_ROLE_REQUIRED") != "1":
        return conn
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT current_user=%s AND session_user=%s
                    AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole
                    AND NOT r.rolreplication AND NOT r.rolbypassrls
                    AND r.rolcanlogin AND r.rolinherit
                    AND (SELECT count(*) FROM pg_auth_members m WHERE m.member=r.oid)=1
                    AND EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles duty ON duty.oid=m.roleid
                        WHERE m.member=r.oid AND duty.rolname=%s AND NOT m.admin_option
                        AND m.inherit_option AND m.set_option AND NOT duty.rolcanlogin
                        AND NOT duty.rolsuper AND NOT duty.rolcreatedb AND NOT duty.rolcreaterole
                        AND NOT duty.rolreplication AND NOT duty.rolbypassrls AND NOT duty.rolinherit)
                    AND NOT has_database_privilege(current_user,current_database(),'CREATE')
                    AND NOT has_schema_privilege(current_user,'public','CREATE') AS allowed
                FROM pg_roles r WHERE r.rolname=current_user
                """,
                (RUNTIME_LOGIN, RUNTIME_LOGIN, RUNTIME_DUTY),
            )
            row = cur.fetchone()
            if row is None or _value(row, "allowed") is not True:
                raise RuntimeError("planner database identity is outside its bounded runtime duty")
        return conn
    except Exception:
        conn.close()
        raise


def verify_schema(conn, tables):
    """Initialization verifies existing structure; only the migrator creates it."""
    with conn.cursor() as cur:
        for table in tables:
            required = SCHEMA_COLUMNS[table]
            cur.execute(
                """
                SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
                WHERE c.oid=to_regclass(%s) AND c.relkind IN ('r','p')
                    AND a.attnum>0 AND NOT a.attisdropped
                """,
                ("public." + table,),
            )
            actual = {_value(row, "attname") for row in cur.fetchall()}
            if not required <= actual:
                raise RuntimeError("planner migration-owned schema is unavailable: " + table)
