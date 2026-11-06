#!/usr/bin/env python3
"""
Verdify MCP Server — Greenhouse control tools for Agent Iris.

Gives Iris direct access to greenhouse data, planner control,
and setpoint management through the standard MCP protocol.

Run: python mcp/server.py
Transport: streamable-http on port 8400
"""

import asyncio
import functools
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import stat
import sys
import time
import urllib.parse
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import asyncpg
from mcp.server.fastmcp import FastMCP
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

# verdify_schemas lives one level up from this server file in every worktree.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
import verdify_schemas.embeddings as _embeddings  # noqa: E402
from verdify_schemas import (  # noqa: E402
    ALL_TUNABLES,
    AlertEnvelope,
    AlertResolvePayload,
    ClimateSnapshot,
    CropCreate,
    CropUpdate,
    EquipmentStateRow,
    EventCreate,
    ForecastSummaryRow,
    HarvestCreate,
    LessonCreate,
    LessonSummary,
    LessonSupersede,
    LessonUpdate,
    LessonValidate,
    ObservationCreate,
    OutcomeKpiActionRow,
    OutcomeKpiCoverage,
    OutcomeKpiResponse,
    Plan,
    PlanDeliveryLogRow,
    PlanEvaluation,
    PlanHypothesisStructured,
    PlanRunResponse,
    PlanStatusJournal,
    PlanStatusResponse,
    PlanStatusWaypoint,
    ScorecardResponse,
    SetpointSummary,
    TreatmentCreate,
    derive_lesson_state,
    is_legal_lesson_transition,
)
from verdify_schemas.climate_intent import (  # noqa: E402
    CLIMATE_INTENT_CONTRACT_VERSION,
    CLIMATE_INTENT_FIELDS,
    ClimateIntent,
    climate_intent_materialization_guardrails,
    materialize_climate_intent_tier1,
)
from verdify_schemas.experiment_config import (  # noqa: E402
    demoted_policy_write_gate,
    submit_policy_proposal,
)
from verdify_schemas.fixed_panel_native_route import read_native_fixed_panel_route_evidence  # noqa: E402
from verdify_schemas.irrigation import LineId, StartKind, Weekday  # noqa: E402
from verdify_schemas.observed_minute_reader import read_observed_minute_evidence  # noqa: E402
from verdify_schemas.plan import (  # noqa: E402
    classify_planner_terminal_action,
    plan_current_coverage_error,
)
from verdify_schemas.policy_vector import WIRE_COMPONENT_INDEXES  # noqa: E402
from verdify_schemas.realized_dryout import (  # noqa: E402
    RealizedDryoutEvidence,
    project_realized_solar_night_dryout,
    read_realized_dryout_evidence,
)
from verdify_schemas.route_only_crop_band_reader import read_route_only_crop_band_evidence  # noqa: E402
from verdify_schemas.telemetry import DliEvidence  # noqa: E402
from verdify_schemas.tunable_registry import (  # noqa: E402
    BAND_OWNED_REG,
    CROP_BAND_REG,
    PLANNER_PUSHABLE_REG,
    TIER1_REG,
    normalize_planner_value,
    registry_value_error,
)
from verdify_services import activity as activity_service  # noqa: E402
from verdify_services import alerts as alerts_service  # noqa: E402
from verdify_services import crops as crop_service  # noqa: E402
from verdify_services import reads as reads_service  # noqa: E402
from verdify_services import settings as settings_service  # noqa: E402
from verdify_services import soil as soil_service  # noqa: E402
from verdify_services.actor import Actor as ServiceActor  # noqa: E402
from verdify_services.problems import Problem, ServiceError, problem  # noqa: E402

# ── Config ──
# Read DB password from .env
_env_path = Path("/srv/verdify/.env")
_db_pass = "verdify"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if line.startswith("POSTGRES_PASSWORD="):
            _db_pass = line.split("=", 1)[1].strip().strip('"').strip("'")
# #24: the MCP server is already DSN-native — it connects via asyncpg against
# DB_DSN, never `docker exec psql`. This IS the VERDIFY_DB_BACKEND=dsn path of
# the shared scripts/lib/psql-verdify.sh contract: setting DB_DSN to an
# in-cluster Postgres endpoint moves this service off the VM with no code change.
# Default below preserves the live VM connection (localhost:5432).
DB_DSN = os.environ.get("DB_DSN", f"postgresql://verdify:{_db_pass}@localhost:5432/verdify")
MCP_RUNTIME_DB_ROLE_REQUIRED_ENV = "VERDIFY_MCP_RUNTIME_DB_ROLE_REQUIRED"
MCP_RUNTIME_DB_LOGIN = "verdify_mcp_runtime_login"
_MCP_RUNTIME_DB_ATTESTATION_SQL = """
SELECT current_user = session_user
   AND current_user = 'verdify_mcp_runtime_login'
   AND pg_catalog.current_setting('search_path') =
       'pg_catalog, public, pg_temp'
   AND public.fn_mcp_runtime_attest_ordinary_login()
"""


def _mcp_runtime_db_role_required() -> bool:
    return os.environ.get(MCP_RUNTIME_DB_ROLE_REQUIRED_ENV, "").strip() == "1"


def _validate_mcp_runtime_dsn() -> None:
    if not _mcp_runtime_db_role_required():
        return
    try:
        dsn_user = urllib.parse.unquote(urllib.parse.urlsplit(DB_DSN).username or "")
        dsn_password = urllib.parse.urlsplit(DB_DSN).password
    except ValueError as exc:
        raise RuntimeError("MCP ordinary database DSN is malformed") from exc
    if (
        not hmac.compare_digest(dsn_user, MCP_RUNTIME_DB_LOGIN)
        or not hmac.compare_digest(os.environ.get("DB_USER", ""), MCP_RUNTIME_DB_LOGIN)
        or not dsn_password
        or not os.environ.get("DB_PASSWORD")
        or os.environ.get("POSTGRES_PASSWORD")
        or os.environ.get("DB_PASS")
    ):
        raise RuntimeError("MCP ordinary database login contract failed")


async def _attest_mcp_runtime_connection(conn: asyncpg.Connection) -> None:
    if _mcp_runtime_db_role_required() and await conn.fetchval(_MCP_RUNTIME_DB_ATTESTATION_SQL) is not True:
        raise RuntimeError("MCP ordinary database attestation failed")


_validate_mcp_runtime_dsn()
SITE_PUBLISH_TRIGGER_PATH = os.environ.get(
    "VERDIFY_SITE_PUBLISH_TRIGGER_PATH",
    "/var/local/verdify/state/plan-publish-trigger",
).strip()
MCP_DB_STATEMENT_TIMEOUT_MS = max(
    1000,
    int(os.environ.get("VERDIFY_MCP_DB_STATEMENT_TIMEOUT_MS", "15000")),
)
# The single-day action scorecard performs four historical setpoint lookups per
# decision. Keep its read bounded without widening every MCP query's DB budget.
KPI_ACTION_SCORECARD_TIMEOUT_MS = 30_000
# Legacy planner.py removed — planning runs via iris_planner.py → Hermes /v1/runs
BAND_OWNED_PARAMS = BAND_OWNED_REG
# P1a (B6): the crop temp/VPD band targets (temp_low/high, vpd_low/high, per-zone
# vpd targets). These are dispatcher/curve-owned and MUST NOT be written to
# setpoint_plan (that caused clamp storms), but the planner's INTENDED band is
# worth recording in the plan_journal audit so v_plan_accuracy / v_plan_compliance
# can grade planned-vs-served band against the new (migration-145) curve. We
# capture them into hypothesis_structured.planned_band WITHOUT actuating them.
CROP_BAND_PARAMS = CROP_BAND_REG
PLAN_REQUIRED_PARAMS = TIER1_REG
TIER1_TUNABLES = TIER1_REG

# A required plan with eight complete ClimateIntent waypoints serializes to
# roughly 5K decoded characters. Keep the advertised MCP argument surface well
# below Hermes's 16K final continuation ceiling so a valid tool call can close
# its JSON before the completion cap. These are transport bounds only:
# registry, materialization, coverage and write-fence validation below remain
# authoritative.
SET_PLAN_HYPOTHESIS_MAX_CHARS = 2200
SET_PLAN_TRANSITIONS_MAX_CHARS = 6000
SET_PLAN_EXPERIMENT_MAX_CHARS = 240
SET_PLAN_EXPECTED_OUTCOME_MAX_CHARS = 320
SET_PLAN_ARGUMENTS_MAX_CHARS = 10000
SET_PLAN_TRANSITIONS_MIN = 3
SET_PLAN_TRANSITIONS_MAX = 8
SET_PLAN_REASON_MAX_CHARS = 120

FORCED_ON_SWITCH_PARAMS = frozenset({"sw_fsm_controller_enabled"})
CLIMATE_TARGET_PARAM_ALIASES = {
    "temp_low_f": "temp_low",
    "temp_high_f": "temp_high",
    "vpd_low_kpa": "vpd_low",
    "vpd_high_kpa": "vpd_high",
}


def _climate_intent_waypoint_errors(waypoints: object) -> list[dict[str, object]]:
    if not isinstance(waypoints, list):
        return [{"transition_index": -1, "error": "transitions must be a JSON array"}]
    errors: list[dict[str, object]] = []
    if not SET_PLAN_TRANSITIONS_MIN <= len(waypoints) <= SET_PLAN_TRANSITIONS_MAX:
        errors.append(
            {
                "transition_index": -1,
                "error": (f"set_plan requires {SET_PLAN_TRANSITIONS_MIN}-{SET_PLAN_TRANSITIONS_MAX} transitions"),
                "transition_count": len(waypoints),
            }
        )
    for idx, wp in enumerate(waypoints):
        if not isinstance(wp, dict):
            errors.append({"transition_index": idx, "error": "transition must be an object"})
            continue
        if "climate_intent" not in wp:
            errors.append({"transition_index": idx, "error": "missing climate_intent"})
        elif isinstance(wp["climate_intent"], dict):
            provided = set(wp["climate_intent"])
            missing = sorted(set(CLIMATE_INTENT_FIELDS) - provided)
            if missing:
                errors.append(
                    {
                        "transition_index": idx,
                        "error": "climate_intent must explicitly set every field",
                        "missing_fields": missing,
                    }
                )
        if "params" in wp and wp.get("params") not in ({}, None):
            errors.append({"transition_index": idx, "error": "raw params are not accepted in set_plan"})
        reason = wp.get("reason")
        if isinstance(reason, str) and len(reason) > SET_PLAN_REASON_MAX_CHARS:
            errors.append(
                {
                    "transition_index": idx,
                    "error": f"reason must be at most {SET_PLAN_REASON_MAX_CHARS} characters",
                    "reason_chars": len(reason),
                }
            )
    return errors


def _set_plan_decoded_argument_chars(
    *,
    plan_id: str,
    hypothesis: str,
    transitions: str,
    experiment: str,
    expected_outcome: str,
    trigger_id: str | None,
    planner_instance: str | None,
    valid_from: str | None,
    expires_at: str | None,
) -> int:
    """Count decoded argument text, independent of transport JSON escaping."""
    return sum(
        len(value)
        for value in (
            plan_id,
            hypothesis,
            transitions,
            experiment,
            expected_outcome,
            trigger_id,
            planner_instance,
            valid_from,
            expires_at,
        )
        if value is not None
    )


async def _fetch_active_tier1_params(conn: asyncpg.Connection) -> dict[str, float]:
    rows = await conn.fetch(
        """
        SELECT parameter, value
          FROM v_active_plan
         WHERE parameter = ANY($1::text[])
        """,
        sorted(set(TIER1_REG) | set(BAND_OWNED_REG)),
    )
    params = {str(row["parameter"]): float(row["value"]) for row in rows}
    target_row = await conn.fetchrow(
        """
        WITH latest_climate AS (
          SELECT ts,
                 temp_avg,
                 vpd_avg,
                 dew_point
            FROM climate
           WHERE temp_avg IS NOT NULL
             AND vpd_avg IS NOT NULL
           ORDER BY ts DESC
           LIMIT 1
        ), target AS (
          SELECT ts,
                 temp_avg,
                 vpd_avg,
                 dew_point,
                 fn_setpoint_at('temp_low', ts) AS temp_low_f,
                 fn_setpoint_at('temp_high', ts) AS temp_high_f,
                 fn_setpoint_at('vpd_low', ts) AS vpd_low_kpa,
                 fn_setpoint_at('vpd_high', ts) AS vpd_high_kpa
            FROM latest_climate
        )
        SELECT temp_avg AS temp_actual_f,
               vpd_avg AS vpd_actual_kpa,
               temp_low_f,
               temp_high_f,
               vpd_low_kpa,
               vpd_high_kpa,
               CASE WHEN dew_point IS NULL THEN NULL ELSE temp_avg - dew_point END AS dew_margin_f,
               greatest(0.0, temp_avg - temp_high_f) AS temp_above_high_f,
               greatest(0.0, vpd_avg - vpd_high_kpa) AS vpd_above_high_kpa,
               (temp_avg - ((temp_low_f + temp_high_f) / 2.0)) AS temp_target_delta_f,
               (vpd_avg - ((vpd_low_kpa + vpd_high_kpa) / 2.0)) AS vpd_target_delta_kpa
          FROM target
        """
    )
    if target_row:
        for key, value in dict(target_row).items():
            if value is not None:
                numeric = float(value)
                params[key] = numeric
                if alias := CLIMATE_TARGET_PARAM_ALIASES.get(key):
                    params[alias] = numeric
    return params


def _materialize_climate_intent_waypoints(
    waypoints: list[object],
    active_tier1_params: dict[str, float],
) -> tuple[list[object], list[dict[str, object]]]:
    expanded: list[object] = []
    intent_records: list[dict[str, object]] = []
    for wp in waypoints:
        if not isinstance(wp, dict) or "climate_intent" not in wp:
            expanded.append(wp)
            continue
        intent = ClimateIntent.model_validate(wp["climate_intent"])
        raw_materialized = materialize_climate_intent_tier1(intent, active_tier1_params)
        # One final normalization boundary intersects the planner-facing and
        # firmware/dispatcher bounds.  No later persistence path clamps again.
        materialized = {name: normalize_planner_value(name, value) for name, value in raw_materialized.items()}
        guardrails = climate_intent_materialization_guardrails(intent, active_tier1_params, materialized)
        expanded_wp = dict(wp)
        expanded_wp["params"] = materialized
        expanded.append(expanded_wp)
        record = {
            "ts": wp.get("ts"),
            "reason": wp.get("reason"),
            "climate_intent": intent.model_dump(mode="json"),
            "materialized_params": materialized,
        }
        if guardrails:
            record["guardrails"] = guardrails
        intent_records.append(record)
    return expanded, intent_records


# ── Lane C (#584): direct-writer demotion ──
# When VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED=0 OR an experiment
# assignment is armed, set_plan/set_tunable stop writing actuation-eligible
# setpoint_plan rows and instead record policy_proposals through the
# migration-208 producer function. Feature-off (legacy enabled + experiment
# env unset) takes no gate query at all — behavior is byte-identical.


def _policy_proposal_components(params: dict[str, float]) -> list[dict]:
    """Wire-schema component rows for the policy params of one write."""
    return [
        {
            "field_name": name,
            "component_index": WIRE_COMPONENT_INDEXES[name],
            "normalized_value": float(value),
        }
        for name, value in sorted(params.items())
        if name in WIRE_COMPONENT_INDEXES
    ]


async def _record_demoted_policy_proposal(
    conn,
    demotion: dict,
    *,
    action: str,
    trigger_ref: str | None,
    params: dict[str, float],
    digest_material: object = None,
) -> str:
    """Persist the demoted write as a proposal; return the tool JSON payload."""
    if not demotion.get("assignment_id"):
        return json.dumps(
            {
                "error": "direct policy writes are demoted but no experiment assignment is armed",
                "detail": "legacy direct writes disabled (VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED=0) "
                "with no active control_assignments row covering now(); nothing was recorded or actuated",
                "actuated": False,
            }
        )
    digest = None
    if digest_material is not None:
        digest = hashlib.sha256(json.dumps(digest_material, sort_keys=True, default=str).encode()).hexdigest()
    try:
        proposal_id = await submit_policy_proposal(
            conn,
            producer="ai",
            trigger_ref=trigger_ref,
            components=_policy_proposal_components(params),
            digest_sha256=digest,
            assignment_id=demotion["assignment_id"],
            actor=f"mcp-{action}",
        )
    except Exception as exc:  # surface, never silently actuate instead
        return json.dumps(
            {
                "error": "policy proposal could not be recorded",
                "detail": f"{type(exc).__name__}: {exc}",
                "actuated": False,
            }
        )
    return json.dumps(
        {
            "ok": True,
            "proposal_recorded": True,
            "actuated": False,
            "proposal_id": proposal_id,
            "action": action,
            "param_count": len(_policy_proposal_components(params)),
            "note": (
                "Proposal recorded, NOT actuated: direct policy writes are demoted while an "
                "experiment assignment is armed (or legacy writes are disabled). The policy "
                "arbiter compiles the proposal against the active assignment (#584)."
            ),
        }
    )


def _json(obj):
    """JSON serialize with asyncpg/Decimal support."""
    import decimal

    def default(o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        if isinstance(o, datetime | date):
            return o.isoformat()
        return str(o)

    return json.dumps(obj, default=default)


# ═══════════════════════════════════════════════════════════════
# SERVER-SIDE AUDIENCE AUTHORIZATION (#585, audit §8.8)
# ═══════════════════════════════════════════════════════════════
# Before this layer the ONLY boundary in front of the 27 registered tools was
# the NetworkPolicy ingress allowlist: the `Authorization: Bearer
# ${VERDIFY_MCP_TOKEN}` header Hermes already sends
# (deploy/k8s/components/hermes-iris/hermes-config.yaml) was never validated.
# The controlled planner experiment's treatment firewall requires
# audience-scoped tool enforcement IN the MCP layer, not just a client-side
# `tools.include` list.
#
# Modes (VERDIFY_MCP_AUTH_MODE):
#   off     — explicit development-only bypass; authorization is fully
#             disabled, byte-identical to the pre-#585 behavior.
#   log     — every would-be denial is emitted as one structured JSON log line
#             (never the token value) and the call proceeds. Prod runs this
#             first to prove the token registry before any blocking.
#   enforce — denials reject the tool call with a clear error. FAIL-CLOSED: a
#             missing or unknown bearer token denies every tool.
# Any unrecognized mode value is treated as "enforce" — a typo must fail
# closed, never silently disable authorization.
#
# Token registry (audience → bearer token) is env-driven, matching the flat
# VERDIFY_MCP_* env config style of this file:
#   VERDIFY_MCP_TOKEN_IRIS       — the current Hermes Iris profile credential
#                                  (same value hermes-config.yaml sends as
#                                  ${VERDIFY_MCP_TOKEN}).
#   VERDIFY_MCP_TOKEN_EXPERIMENT — the experiment-arm planner profile
#                                  (wired in Lane C/D tranche 2).
#   VERDIFY_MCP_TOKEN_ADMIN      — operator/debug credential; all tools.
#   VERDIFY_MCP_TOKEN_OBSERVER   — protocol-only availability probe; no tools.
# Tokens must be distinct per audience and valid RFC 6750 bearer credentials.
# Invalid and duplicate entries are excluded from the registry. Token values
# are never logged, never echoed in errors, and never surfaced by /readyz
# (environment-variable names only).
#
# Named agents (VD-59, architecture §5.6) are the fifth audience, `agent`. Their
# keys never come from the environment: each agent is one file in the mounted
# Secret verdify-mcp-agents (agent_key_registry below), so VERDIFY_MCP_TOKEN_AGENT
# stays an unrecognized variable and a nameless agent cannot exist.

VERDIFY_MCP_AUTH_MODE_ENV = "VERDIFY_MCP_AUTH_MODE"
VERDIFY_MCP_AUTH_MODES = ("off", "log", "enforce")
_AUDIENCE_TOKEN_ENV_PREFIX = "VERDIFY_MCP_TOKEN_"
AGENT_AUDIENCE = "agent"
KNOWN_AUDIENCES = frozenset({"iris", "experiment", "admin", "observer", AGENT_AUDIENCE})
# The environment-token parser keeps its four audiences.
_AUDIENCE_TOKEN_ENV_NAMES = {
    f"{_AUDIENCE_TOKEN_ENV_PREFIX}{audience.upper()}": audience for audience in KNOWN_AUDIENCES - {AGENT_AUDIENCE}
}
# RFC 6750 b64token. Besides empty/whitespace/control values, this rejects
# characters that cannot be represented as one unambiguous Bearer credential.
_BEARER_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/\-]+=*")

_TOOL_AUTH_LOGGER = logging.getLogger("verdify.mcp.auth")
_TOOL_AUDIT_LOGGER = logging.getLogger("verdify.mcp.audit")
_TOOL_AUDIT_LOGGER.setLevel(logging.INFO)
_MCP_PATH = "/mcp"


class ToolAccessDenied(Exception):
    """Raised in enforce mode; surfaces to the MCP client as a tool error."""


def auth_mode() -> str:
    """Resolve the authorization mode from the environment.

    Absent, empty, or unrecognized values fail CLOSED to ``enforce``. Only an
    explicit supported value can select the development ``off`` bypass.
    """
    raw = os.environ.get(VERDIFY_MCP_AUTH_MODE_ENV, "").strip().lower()
    if not raw:
        return "enforce"
    return raw if raw in VERDIFY_MCP_AUTH_MODES else "enforce"


def audience_token_configuration() -> tuple[dict[str, str], list[str], list[str], list[str]]:
    """Read and validate VERDIFY_MCP_TOKEN_<AUDIENCE> env vars.

    Returns (unambiguous audience → token, unrecognized env var NAMES,
    duplicate-token env var NAMES, invalid-token env var NAMES). Duplicate
    values remove every affected audience from the matchable registry, so an
    accidentally shared admin credential can never resolve by lexical
    ordering. Values never leave this function except in the validated
    registry and are never logged/readied.
    """
    candidates: dict[str, tuple[str, str]] = {}
    unrecognized: list[str] = []
    invalid: list[str] = []
    for key in sorted(os.environ):
        if not key.startswith(_AUDIENCE_TOKEN_ENV_PREFIX):
            continue
        audience = _AUDIENCE_TOKEN_ENV_NAMES.get(key)
        if audience is None:
            unrecognized.append(key)
            continue
        token = os.environ[key]
        if _BEARER_TOKEN_PATTERN.fullmatch(token) is None:
            invalid.append(key)
            continue
        candidates[audience] = (key, token)

    audiences_by_token: dict[str, list[str]] = {}
    for audience, (_key, token) in candidates.items():
        audiences_by_token.setdefault(token, []).append(audience)
    duplicate_audiences = {
        audience for audiences in audiences_by_token.values() if len(audiences) > 1 for audience in audiences
    }
    duplicate_env_names = sorted(candidates[audience][0] for audience in duplicate_audiences)
    registry = {
        audience: token for audience, (_key, token) in candidates.items() if audience not in duplicate_audiences
    }
    return registry, unrecognized, duplicate_env_names, invalid


def audience_token_registry() -> tuple[dict[str, str], list[str]]:
    """Compatibility view of only the unambiguous audience token registry."""
    registry, unrecognized, _duplicate_env_names, _invalid_token_env_names = audience_token_configuration()
    return registry, unrecognized


def resolve_token_audience(token: str | None, registry: dict[str, str]) -> str | None:
    """Map a presented bearer token to an audience, or None.

    hmac.compare_digest gives a constant-time comparison per candidate, and the
    loop always scans the full registry (no early exit) so response timing does
    not reveal which audience matched. ``audience_token_configuration`` removes
    duplicate values before this function can match them.
    """
    if not token:
        return None
    token_bytes = token.encode()
    matched: str | None = None
    for audience in sorted(registry):
        if hmac.compare_digest(token_bytes, registry[audience].encode()) and matched is None:
            matched = audience
    return matched


# ── Named agent keys (VD-59) ──
# Secret verdify-mcp-agents holds one key per named in-cluster agent: the key is
# the agent's name and its value the agent's bearer token. The Deployment mounts
# it read-only and optional, without subPath (a subPath mount never sees Secret
# updates), so the kubelet's atomic `..data` swap delivers every add, removal or
# rotation into the running pods. MCP re-reads the directory at most every
# AGENT_KEYS_REFRESH_SECONDS: no restart and no device-gated sync admits or cuts
# off one agent. Names and values are validated here; values never leave the
# returned registry.
VERDIFY_MCP_AGENT_KEYS_DIR_ENV = "VERDIFY_MCP_AGENT_KEYS_DIR"
DEFAULT_MCP_AGENT_KEYS_DIR = "/etc/verdify/mcp-agents"
AGENT_KEYS_REFRESH_SECONDS = 60.0
# The OpenAPI Actor source pattern (agent:<name>), capped at 63 characters.
_AGENT_NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
# `admin` is the environment admin credential's actor name (agent:admin).
_RESERVED_AGENT_NAMES = frozenset({"admin"})
_AGENT_KEY_MAX_BYTES = 4096
_AGENT_KEY_WHITESPACE = " \t\n\r\x0b\x0c"


@dataclass(frozen=True)
class _AgentKeyScan:
    directory: str
    loaded_at: float
    keys: dict[str, str]
    rejected: frozenset[str]


_agent_key_scan: _AgentKeyScan | None = None


def _agent_keys_clock() -> float:
    """The agent-key cache clock: time.monotonic(), a seam for the cache tests."""
    return time.monotonic()


def _read_agent_key(path: str) -> str | None:
    """One key file's token, or None when the file is not one bearer token.

    Raises FileNotFoundError for a name that vanished: between the kubelet's
    `..data` swap and its cleanup, a removed key's top-level link dangles.
    """
    info = os.stat(path)  # follows the kubelet's `<name> -> ..data/<name>` link
    if not stat.S_ISREG(info.st_mode) or info.st_size > _AGENT_KEY_MAX_BYTES:
        return None
    with open(path, "rb") as handle:
        raw = handle.read(_AGENT_KEY_MAX_BYTES + 1)
    if len(raw) > _AGENT_KEY_MAX_BYTES:
        return None
    try:
        token = raw.decode("ascii").strip(_AGENT_KEY_WHITESPACE)
    except UnicodeDecodeError:
        return None
    return token if _BEARER_TOKEN_PATTERN.fullmatch(token) else None


def _scan_agent_key_directory(directory: str) -> tuple[dict[str, str], set[str]]:
    """Read every agent key file in ``directory``; reject by name, never by value."""
    try:
        names = sorted(os.listdir(directory))
    except (FileNotFoundError, NotADirectoryError):
        return {}, set()  # no Secret mounted: no agents
    except OSError as exc:
        _TOOL_AUTH_LOGGER.warning(
            "%s", _json({"event": "mcp_agent_keys_unreadable", "error_class": type(exc).__name__})
        )
        return {}, set()
    tokens: dict[str, str] = {}
    rejected: set[str] = set()
    for name in names:
        if name.startswith("."):
            continue  # the kubelet's `..data` link and its timestamped payload directory
        if _AGENT_NAME_PATTERN.fullmatch(name) is None or name in _RESERVED_AGENT_NAMES:
            rejected.add(name)
            continue
        try:
            token = _read_agent_key(os.path.join(directory, name))
        except FileNotFoundError:
            continue  # removed mid-swap: absent, not rejected
        except OSError:
            token = None
        if token is None:
            rejected.add(name)
        else:
            tokens[name] = token
    names_by_token: dict[str, list[str]] = {}
    for name, token in tokens.items():
        names_by_token.setdefault(token, []).append(name)
    shared = {name for sharing in names_by_token.values() if len(sharing) > 1 for name in sharing}
    return {name: token for name, token in tokens.items() if name not in shared}, rejected | shared


def agent_key_registry() -> tuple[dict[str, str], list[str]]:
    """Named agent keys: (agent name → token, rejected agent names).

    Reads the directory in VERDIFY_MCP_AGENT_KEYS_DIR (default
    /etc/verdify/mcp-agents) and caches the scan for 60 s on time.monotonic(),
    so a key added to or removed from the mounted Secret is seen within a minute
    of its file changing. One file per agent: the file name is the agent name,
    the content its token with surrounding whitespace stripped; names starting
    with `.` are skipped and a missing or empty directory means no agents.

    Rejected by name only, never touching the environment audiences: names
    outside the Actor source pattern, the reserved name admin, a token that is
    not one RFC 6750 bearer credential, a token shared by two names (both
    dropped) and a token equal to an environment audience's token (the agent
    key dropped). Rejections never change readiness.
    """
    global _agent_key_scan
    directory = os.environ.get(VERDIFY_MCP_AGENT_KEYS_DIR_ENV, "").strip() or DEFAULT_MCP_AGENT_KEYS_DIR
    now = _agent_keys_clock()
    scan = _agent_key_scan
    if scan is None or scan.directory != directory or now - scan.loaded_at >= AGENT_KEYS_REFRESH_SECONDS:
        keys, scan_rejected = _scan_agent_key_directory(directory)
        scan = _AgentKeyScan(directory=directory, loaded_at=now, keys=keys, rejected=frozenset(scan_rejected))
        _agent_key_scan = scan
    environment_registry, _unrecognized, _duplicates, _invalid = audience_token_configuration()
    environment_tokens = [token.encode() for token in environment_registry.values()]
    registry: dict[str, str] = {}
    rejected = set(scan.rejected)
    for name, token in scan.keys.items():
        token_bytes = token.encode()
        collides = False
        for environment_token in environment_tokens:
            collides = hmac.compare_digest(token_bytes, environment_token) or collides
        if collides:
            rejected.add(name)
        else:
            registry[name] = token
    return registry, sorted(rejected)


def resolve_caller(token: str | None) -> tuple[str | None, str | None]:
    """Map a presented bearer token to (audience, agent name), or (None, None).

    The environment audiences resolve first through resolve_token_audience,
    then the named agent keys through the same full-scan hmac.compare_digest
    loop. Both registries are always scanned in full, so timing does not reveal
    which side (if any) matched; agent_key_registry() drops an agent key equal
    to an environment token, so at most one side can match. A removed key
    resolves to nothing once the cache refreshes, at the transport and at
    dispatch alike.
    """
    registry, _unrecognized, _duplicate_env_names, _invalid_token_env_names = audience_token_configuration()
    agents, _rejected = agent_key_registry()
    audience = resolve_token_audience(token, registry)
    agent_name = resolve_token_audience(token, agents)
    if audience is not None:
        return audience, None
    if agent_name is not None:
        return AGENT_AUDIENCE, agent_name
    return None, None


# ── Request actor (VD-59, architecture §5.6) ──
@dataclass(frozen=True)
class McpActor:
    """Who called: the R4 source string and the actor's name.

    iris → (iris, iris); a named agent → (agent:<name>, <name>); the admin
    credential → (agent:admin, admin). Writes build verdify_services.Actor
    (source, name, None) from it.
    """

    source: str
    name: str


_REQUEST_ACTOR: ContextVar[McpActor | None] = ContextVar("verdify_mcp_request_actor", default=None)


def request_actor(audience: str | None, agent_name: str | None = None) -> McpActor | None:
    """The actor a resolved caller writes as; None for experiment, observer or no caller."""
    if audience == "iris":
        return McpActor(source="iris", name="iris")
    if audience == AGENT_AUDIENCE and agent_name:
        return McpActor(source=f"agent:{agent_name}", name=agent_name)
    if audience == "admin":
        return McpActor(source="agent:admin", name="admin")
    return None


def current_actor() -> McpActor | None:
    """The in-flight tool call's actor, or None (auth mode off, or no resolved caller).

    The only way a tool learns who called: no tool argument sets a source, an
    actor or a *_by column. A write that records an actor refuses with
    `forbidden` when this is None (Iris's unchanged set_plan and set_tunable
    excepted).
    """
    return _REQUEST_ACTOR.get()


def _bearer_token_from_authorization(value: str | None) -> str | None:
    """Parse one Authorization header without ever retaining or logging it."""
    scheme, separator, credential = (value or "").partition(" ")
    if not separator or scheme.lower() != "bearer":
        return None
    return credential.strip() or None


def _transport_request_denial(token: str | None) -> dict[str, str | None] | None:
    """Return a transport-authentication denial, or None for a known caller."""
    _registry, _unrecognized, duplicate_env_names, _invalid_token_env_names = audience_token_configuration()
    if duplicate_env_names:
        return {"audience": None, "reason": "duplicate_token_configuration"}
    audience, _agent_name = resolve_caller(token)
    if audience is not None:
        return None
    return {
        "audience": None,
        "reason": "missing_token" if not token else "unknown_token",
    }


def _transport_bearer_token(scope) -> str | None:
    """Extract exactly one bearer credential from raw ASGI request headers."""
    authorization_values = [
        value.decode("latin-1") for name, value in scope.get("headers", []) if name.lower() == b"authorization"
    ]
    if len(authorization_values) != 1:
        return None
    return _bearer_token_from_authorization(authorization_values[0])


async def _send_transport_unauthorized(send) -> None:
    """Send a deliberately generic 401 without exposing token or inventory data."""
    body = b'{"error":"unauthorized"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class MCPTransportAuthMiddleware:
    """Authenticate every MCP HTTP request before FastMCP creates a session.

    This is raw ASGI middleware rather than ``BaseHTTPMiddleware`` so streaming
    request/response channels are not buffered or replaced. The plain
    ``/readyz`` custom route intentionally stays public for Kubernetes probes.
    Per-tool audience authorization remains the second enforcement layer after
    a known audience reaches the protocol endpoint.
    """

    def __init__(self, app, *, mcp_path: str = _MCP_PATH) -> None:
        self.app = app
        self.mcp_path = mcp_path.rstrip("/") or "/"

    async def __call__(self, scope, receive, send) -> None:
        path = str(scope.get("path", "")).rstrip("/") or "/"
        if scope.get("type") != "http" or path != self.mcp_path:
            await self.app(scope, receive, send)
            return

        mode = auth_mode()
        if mode == "off":
            await self.app(scope, receive, send)
            return

        denial = _transport_request_denial(_transport_bearer_token(scope))
        if denial is None:
            await self.app(scope, receive, send)
            return

        enforced = mode == "enforce" or denial["reason"] == "duplicate_token_configuration"
        _TOOL_AUTH_LOGGER.warning(
            "%s",
            _json(
                {
                    "event": "mcp_transport_authn_denial",
                    "mode": mode,
                    "enforced": enforced,
                    **denial,
                }
            ),
        )
        if enforced:
            await _send_transport_unauthorized(send)
            return
        await self.app(scope, receive, send)


def _tool_call_denial(name: str, token: str | None, caller: tuple[str | None, str | None] | None = None) -> dict | None:
    """Return a denial record for this (tool, token), or None when allowed.

    ``caller`` is the token's resolve_caller() result when the dispatcher
    already holds it, so authorization and attribution share one resolution.
    """
    _registry, _unrecognized, duplicate_env_names, _invalid_token_env_names = audience_token_configuration()
    if duplicate_env_names:
        return {
            "tool": name,
            "audience": None,
            "reason": "duplicate_token_configuration",
        }
    audience, agent_name = resolve_caller(token) if caller is None else caller
    if audience is None:
        return {
            "tool": name,
            "audience": None,
            "reason": "missing_token" if not token else "unknown_token",
        }
    # A named agent's denial also names the agent (never its token).
    caller_fields = {"audience": audience, **({"agent": agent_name} if agent_name else {})}
    allowed_audiences = TOOL_AUDIENCES.get(name)
    if allowed_audiences is None:
        # A tool outside the inventory is never authorizable — the startup
        # assertion makes this unreachable in a correctly built image, but a
        # denial here keeps the layer fail-closed regardless.
        return {"tool": name, **caller_fields, "reason": "tool_not_in_audience_registry"}
    if audience not in allowed_audiences:
        return {"tool": name, **caller_fields, "reason": "tool_not_in_audience"}
    return None


def authorize_tool_call(name: str, token: str | None, caller: tuple[str | None, str | None] | None = None) -> None:
    """Gatekeeper for every tool dispatch. Raises ToolAccessDenied in enforce mode."""
    mode = auth_mode()
    if mode == "off":
        return
    denial = _tool_call_denial(name, token, caller)
    if denial is None:
        return
    # Structured, grep-able denial record. Contains audience/tool/reason —
    # NEVER the presented token.
    enforced = mode == "enforce" or denial["reason"] == "duplicate_token_configuration"
    _TOOL_AUTH_LOGGER.warning(
        "%s",
        _json({"event": "mcp_tool_authz_denial", "mode": mode, "enforced": enforced, **denial}),
    )
    if enforced:
        audience = denial["audience"] or "none (unknown or missing bearer token)"
        raise ToolAccessDenied(
            f"unauthorized: tool '{name}' is not available to this credential "
            f"(audience: {audience}). Server-side audience authorization is in "
            f"fail-closed mode (#585)."
        )


def _request_bearer_token(server) -> str | None:
    """Extract the bearer token of the in-flight MCP request, if any.

    The streamable-http transport attaches the originating Starlette Request to
    every message (ServerMessageMetadata.request_context), and the low-level
    server publishes it through the request_ctx contextvar for the duration of
    each handler call — so this is per-request-correct even on a long-lived
    session. Absent context (stdio, in-process calls, import stubs) resolves to
    None, which under enforce mode denies: fail closed.
    """
    lowlevel = getattr(server, "_mcp_server", None)
    if lowlevel is None:
        return None
    try:
        ctx = lowlevel.request_context
    except LookupError:
        return None
    headers = getattr(getattr(ctx, "request", None), "headers", None)
    if headers is None:
        return None
    return _bearer_token_from_authorization(headers.get("authorization"))


# Conservative capability classification: mixed-action tools remain write-capable.
_TOOL_READ_ONLY = frozenset(
    {
        "climate",
        "scorecard",
        "outcome_kpi",
        "equipment_state",
        "forecast",
        "get_setpoints",
        "plan_status",
        "history",
        "lessons",
        "lessons_search",
        "knowledge_search",
        "topology",
        "position_current",
        "crop_history",
        "crop_lifecycle",
        "irrigation_status",
        "irrigation_schedule",
        "irrigation_history",
        "get_greenhouse_snapshot",
        "settings",
        "activity",
        "band_profile",
        "band_profile_preview",
        "soil_targets",
    }
)


def _tool_transport_peer(server):
    """Record only the transport peer; forwarded claims are never promoted to identity."""
    try:
        request = server._mcp_server.request_context.request
    except (AttributeError, LookupError):
        return None
    host = getattr(getattr(request, "client", None), "host", None)
    try:
        return str(ipaddress.ip_address(host))
    except (TypeError, ValueError):
        return None


class AudienceAuthorizedFastMCP(FastMCP):
    """FastMCP with server-side audience authorization at the dispatch choke point.

    FastMCP.call_tool is the single public method the low-level MCP server
    invokes for every tool call, so overriding it covers every registered
    tool without touching any tool body or signature (and without a
    per-tool decorator or ASGI-middleware fallback). A raised ToolAccessDenied is
    converted by the low-level server into an isError tool result with this
    message — the session stays healthy.

    The caller is resolved once per call (resolve_caller) and drives both the
    authorization decision and the request actor (current_actor), which is set
    for the duration of the tool body and reset afterwards (VD-59).
    """

    async def call_tool(self, name, arguments):
        token = _request_bearer_token(self)
        caller: tuple[str | None, str | None] = (None, None)
        if auth_mode() != "off":
            caller = resolve_caller(token)
        authorize_tool_call(name, token, caller)
        audience, agent_name = caller
        actor = request_actor(audience, agent_name)
        peer = _tool_transport_peer(self)
        started = time.monotonic()
        outcome = "exception"
        actor_reset = _REQUEST_ACTOR.set(actor)
        try:
            result = await super().call_tool(name, arguments)
            # Completed is a dispatch outcome, never a claim that a business action succeeded.
            outcome = "tool_error" if getattr(result, "isError", False) is True else "completed"
            return result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            _REQUEST_ACTOR.reset(actor_reset)
            _TOOL_AUDIT_LOGGER.info(
                "%s",
                _json(
                    {
                        "event": "mcp_tool_call",
                        "audience": audience,
                        # The R4 source string only; never a token.
                        "actor": actor.source if actor is not None else None,
                        "tool": name if name in TOOL_AUDIENCES else "unregistered",
                        "capability": "read" if name in _TOOL_READ_ONLY else "write_capable",
                        "result": outcome,
                        "duration_ms": round((time.monotonic() - started) * 1000, 3),
                        "peer_ip": peer,
                        "client_ip_source": "transport_peer" if peer else "unavailable",
                        "auth_mode": auth_mode(),
                    }
                ),
            )

    async def list_tools_unfiltered(self):
        """Internal inventory for readiness/registry checks, never protocol dispatch."""
        return await super().list_tools()

    async def list_tools(self):
        """Expose only the in-flight request audience's authorized inventory."""
        tools = await self.list_tools_unfiltered()
        mode = auth_mode()
        if mode == "off":
            return tools
        _registry, _unrecognized, duplicate_env_names, _invalid_token_env_names = audience_token_configuration()
        if duplicate_env_names:
            return []
        audience, _agent_name = resolve_caller(_request_bearer_token(self))
        if audience is None:
            return []
        allowed = audience_allowlist(audience)
        return [tool for tool in tools if tool.name in allowed]

    def streamable_http_app(self):
        """Wrap the entire protocol endpoint before initialize/list dispatch."""
        return MCPTransportAuthMiddleware(
            super().streamable_http_app(),
            mcp_path=self.settings.streamable_http_path,
        )


# Every backticked tool name here must be registered: tests/test_mcp_irrigation_read_tools.py fails on one that
# tools/list for admin does not return, so the paragraph grows with each tool.
SERVER_INSTRUCTIONS = """Verdify greenhouse control tools. Use these to monitor climate,
    manage setpoints, run the AI planner, and review performance.
    The greenhouse has temp/VPD bands, misters, fog, fans, heaters, and a vent.
    The planner emits bounded ClimateIntent in set_plan transitions; MCP
    materializes it into registry-valid tunables that shape how the controller responds.
    Band params (temp_low, temp_high, vpd_low, vpd_high) are dispatcher-owned
    read-only context in routine plans. Temp comes from crop policy; house VPD is
    derived from crop + zone policy. Use direct tunable pushes only for explicit overrides.

    `set_setting` changes any writable setting within registry min/max and stores the
    `manual` flag it carries (default false); `settings` and `activity` read the catalog,
    history and every write; Iris's `set_setting` on a manual setting is refused with `manual`.

    The house band is operator-owned and has no write tool: `band_profile` reads its profile,
    anchors and day curve with the last 24 h measured, and `band_profile_preview` evaluates a
    draft without saving it. The dashboard saves the band.

    Irrigation: `irrigation_status`, `irrigation_schedule` and `irrigation_history` only read
    the water program, in the operator API's JSON, with unknown as null, never 0. Iris, named
    agents and admin share the writes, with the dashboard's checks: `irrigation_schedule_set`,
    `irrigation_line_set`, `irrigation_run`, `irrigation_stop` and `irrigation_clear_fault`.
    Before the irrigation firmware, run works on L01 only, clear-fault waits for it, schedule and
    line writes are stored for the first program push, and irrigation_stop always works. `manual`:
    MCP creates and definition edits store false unless you pass true; pause, resume, run, stop
    and clear-fault never change it; only Iris is refused with `manual` on a manual item.
    `check_required`: only a person records a line's check, on the dashboard. `request_id` makes
    a write safe to retry: the same id and payload returns the stored result with `replayed: true`.
    A refusal is the Problem JSON with isError: its code is from the OpenAPI ProblemCode list, and
    `retryable` is true only where waiting helps. A value outside a tool's schema is refused before
    the tool runs, with isError and a plain message. Each write says the same: Changes watering
    only; it does not count as your plan for this trigger. The reads do not count either."""

mcp = AudienceAuthorizedFastMCP(
    "verdify",
    instructions=SERVER_INSTRUCTIONS,
    # Bind explicitly so MCP_HTTP_HOST/PORT env vars actually take effect.
    # FastMCP only auto-reads FASTMCP_-prefixed env vars, so the
    # os.environ.setdefault block in __main__ was dead code. Reading the env
    # here lets a systemd drop-in (Environment=MCP_HTTP_HOST=0.0.0.0) make
    # the server reachable from the hermes-iris Docker container via the
    # docker0 / verdify-internal bridge IP. Default stays 127.0.0.1:8000.
    host=os.environ.get("MCP_HTTP_HOST", "127.0.0.1"),
    port=int(os.environ.get("MCP_HTTP_PORT", "8000")),
    # MCP Python SDK 1.27 supports stateless Streamable HTTP for legacy clients.
    # Verdify uses no server-initiated sampling/roots/elicitation, so every
    # authenticated request can safely land on either prod replica without an
    # in-memory Mcp-Session-Id and cross-pod 404s.
    stateless_http=True,
)

HERMES_REQUIRED_TOOLS = frozenset(
    {
        "acknowledge_trigger",
        "activity",
        "alerts",
        "climate",
        "crop_history",
        "crop_lifecycle",
        "crops",
        "equipment_state",
        "forecast",
        "get_setpoints",
        "history",
        "irrigation_clear_fault",
        "irrigation_history",
        "irrigation_line_set",
        "irrigation_run",
        "irrigation_schedule",
        "irrigation_schedule_set",
        "irrigation_status",
        "irrigation_stop",
        "knowledge_search",
        "lessons",
        "lessons_manage",
        "lessons_search",
        "observations",
        "plan_evaluate",
        "plan_status",
        "position_current",
        "scorecard",
        "set_plan",
        "set_setting",
        "set_tunable",
        "settings",
        "topology",
    }
)

# ── Tool inventory as data (#585) ──
# EVERY @mcp.tool() registered in this file must have an entry here — the
# module-bottom _assert_tool_audience_registry_complete() fails startup on any
# drift, so a new tool cannot silently skip authorization.
#
# Audiences:
#   iris       — the current Hermes Iris profile. Exactly the 33 tools of
#                hermes-config.yaml tools.include (== HERMES_REQUIRED_TOOLS);
#                tests/test_mcp_audience_auth.py holds the three surfaces in
#                lockstep. Current behavior is preserved.
#   experiment — the blinded experiment planner arm (audit §8.8): qualified
#                climate/forecast/crop/topology READS plus trigger
#                acknowledgement only. Treatment-revealing reads
#                (get_setpoints, plan_status, history, scorecard, outcome_kpi,
#                lessons*) and all ordinary writes are denied. Wired but
#                unused until Lane C/D tranche 2 issues its credential.
#   agent      — named in-cluster agents (VD-59, architecture §5.6), one key
#                each in Secret verdify-mcp-agents (agent_key_registry). The
#                live greenhouse reads plus alerts, settings, activity, set_setting,
#                every irrigation tool and the planting tools (#1328). Never the
#                planner, lessons, the legacy crops/observations tools or the
#                Slack writers. Calls are attributed agent:<name>.
#   admin      — operator/debug credential; every tool. Its writes are
#                attributed agent:admin.
#   observer   — protocol-only availability probe; no tools.
TOOL_AUDIENCES: dict[str, frozenset[str]] = {
    # Monitoring
    "climate": frozenset({"iris", "experiment", "agent", "admin"}),
    "scorecard": frozenset({"iris", "agent", "admin"}),
    "outcome_kpi": frozenset({"admin"}),
    "equipment_state": frozenset({"iris", "agent", "admin"}),
    "forecast": frozenset({"iris", "experiment", "agent", "admin"}),
    "get_setpoints": frozenset({"iris", "agent", "admin"}),
    "plan_status": frozenset({"iris", "agent", "admin"}),
    "history": frozenset({"iris", "agent", "admin"}),
    "alerts": frozenset({"iris", "agent", "admin"}),
    "settings": frozenset({"iris", "agent", "admin"}),
    "activity": frozenset({"iris", "agent", "admin"}),
    # Irrigation reads (VD-60, #1173): the REST JSON of API-IRR-01, -02, -10 and -11.
    "irrigation_status": frozenset({"iris", "agent", "admin"}),
    "irrigation_schedule": frozenset({"iris", "agent", "admin"}),
    "irrigation_history": frozenset({"iris", "agent", "admin"}),
    # #1346: the greenhouse snapshot (Now's AI-written summary). Never Iris or the experiment arm: the snapshot is
    # never an input to a plan.
    "get_greenhouse_snapshot": frozenset({"agent", "admin"}),
    # Knowledge
    "lessons": frozenset({"iris", "admin"}),
    "lessons_search": frozenset({"iris", "admin"}),
    "knowledge_search": frozenset({"iris", "admin"}),
    # Crops + topology (read-only crop context is experiment-safe; the
    # action-verb crops/observations tools carry writes, so they are not)
    "crops": frozenset({"iris", "admin"}),
    "observations": frozenset({"iris", "admin"}),
    "topology": frozenset({"iris", "experiment", "agent", "admin"}),
    "position_current": frozenset({"iris", "experiment", "agent", "admin"}),
    "crop_history": frozenset({"iris", "experiment", "admin"}),
    "crop_lifecycle": frozenset({"iris", "experiment", "admin"}),
    # Writes
    "set_plan": frozenset({"iris", "admin"}),
    "set_tunable": frozenset({"iris", "admin"}),
    "set_setting": frozenset({"iris", "agent", "admin"}),
    # Lane C (#584): the experiment arm's only actuation-eligible output — an
    # opaque policy_template_id proposal (registered with tranche 2, audit §8.8).
    "policy_template_propose": frozenset({"experiment", "admin"}),
    "acknowledge_trigger": frozenset({"iris", "experiment", "admin"}),
    "plan_evaluate": frozenset({"iris", "admin"}),
    "lessons_manage": frozenset({"iris", "admin"}),
    # Operator-only surfaces (never in the Hermes include list)
    "plan_run": frozenset({"admin"}),
    "query": frozenset({"admin"}),
    # Irrigation writes (VD-64, #1177; VD-61, #1174): one surface for every agent, Iris included (D-IRP-21).
    # They never touch a trigger, so Iris still ends each one with set_plan or acknowledge_trigger.
    "irrigation_schedule_set": frozenset({"iris", "agent", "admin"}),
    "irrigation_line_set": frozenset({"iris", "agent", "admin"}),
    "irrigation_run": frozenset({"iris", "agent", "admin"}),
    "irrigation_stop": frozenset({"iris", "agent", "admin"}),
    "irrigation_clear_fault": frozenset({"iris", "agent", "admin"}),
    # Plantings (#1328): records only (no setting, band or target), over verdify_services.crops. Named agents and
    # admin; Iris stays read-only on plantings and the experiment arm gets none.
    "crop_locations": frozenset({"agent", "admin"}),
    "resolve_positions": frozenset({"agent", "admin"}),
    "record_planting": frozenset({"agent", "admin"}),
    "record_plantings": frozenset({"agent", "admin"}),
    "move_planting": frozenset({"agent", "admin"}),
    "harvest_planting": frozenset({"agent", "admin"}),
    "remove_planting": frozenset({"agent", "admin"}),
    "annotate_planting": frozenset({"agent", "admin"}),
    "planting_history": frozenset({"agent", "admin"}),
    # The house band (#1334): API-23's read for named agents and admin. No band write tool exists (the band is
    # operator-owned; API-24 is the dashboard's), and Iris is left out so its profile stays the Hermes include list.
    "band_profile": frozenset({"agent", "admin"}),
    "band_profile_preview": frozenset({"agent", "admin"}),
    # Soil-moisture targets (#1345): API-SOIL-01/02's twins for named agents and admin. Iris is left out: a target
    # moves the soil_dryout page, which is an operator setting, not a plan.
    "soil_targets": frozenset({"agent", "admin"}),
    "soil_target_set": frozenset({"agent", "admin"}),
}

# Registry entries for tools that are DESIGNED but not yet registered. Each
# moves into TOOL_AUDIENCES when its @mcp.tool() lands; until then the startup
# assertion keeps the two maps disjoint so a pending entry cannot mask a
# registered tool that skipped authorization. (policy_template_propose moved
# into TOOL_AUDIENCES with Lane C tranche 2, #584.)
PENDING_TOOL_AUDIENCES: dict[str, frozenset[str]] = {}


def audience_allowlist(audience: str) -> frozenset[str]:
    """The set of registered tools an audience may call."""
    return frozenset(name for name, audiences in TOOL_AUDIENCES.items() if audience in audiences)


async def _db() -> asyncpg.Connection:
    # Hermes's MCP client timeout is intentionally longer than the database
    # query budget. A server-side fence prevents a timed-out tool call from
    # leaving a PostgreSQL backend consuming memory until it reaches a send
    # boundary.
    conn = await asyncpg.connect(
        DB_DSN,
        server_settings={
            "application_name": "verdify-mcp",
            "statement_timeout": f"{MCP_DB_STATEMENT_TIMEOUT_MS}ms",
        },
    )
    try:
        await _attest_mcp_runtime_connection(conn)
    except Exception:
        await conn.close()
        raise
    return conn


# ── Service-layer tools (architecture §5.6) ──
# Thin adapters over verdify_services: the REST JSON from the same Pydantic
# models, a refusal as its Problem JSON with isError, and the actor only from
# the request. The new tools carry no return annotation, so FastMCP passes the
# CallToolResult through unchanged.


def _tool_result(body: dict[str, Any], *, is_error: bool = False):
    # Imported here: CI loads this module under a FastMCP stub without the MCP runtime.
    import mcp.types as mcp_types

    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=json.dumps(body))],
        isError=is_error,
    )


def _tool_ok(model, replayed: bool = False):
    """The REST JSON of ``model``, with a top-level ``replayed: true`` on a replay (MCP has no headers)."""
    body = model.model_dump(mode="json")
    return _tool_result({**body, "replayed": True} if replayed else body)


def _tool_problem(refusal: Problem):
    """The Problem JSON REST sends as application/problem+json, with isError."""
    return _tool_result(refusal.body(), is_error=True)


def _answers_problems(tool):
    """Answer a service refusal, a query over the database's limit or a database outage as Problem JSON."""

    @functools.wraps(tool)
    async def answered(*args, **kwargs):
        try:
            return await tool(*args, **kwargs)
        except ServiceError as refused:
            return _tool_problem(refused.problem)
        except asyncpg.QueryCanceledError:
            limit_s = MCP_DB_STATEMENT_TIMEOUT_MS // 1000
            return _tool_problem(problem("query_timeout", f"The database stopped the query at its {limit_s} s limit."))
        except (
            OSError,
            asyncpg.PostgresConnectionError,
            asyncpg.InterfaceError,
            # Starting up or shutting down (57P0x), out of connections or memory (53xxx).
            asyncpg.OperatorInterventionError,
            asyncpg.InsufficientResourcesError,
        ):
            return _tool_problem(problem("unavailable", "The database could not answer. Try again."))

    return answered


@asynccontextmanager
async def _service_connection() -> AsyncIterator[asyncpg.Connection]:
    conn = await _db()
    try:
        yield conn
    finally:
        await conn.close()


def _service_actor() -> ServiceActor | None:
    """The request's actor as verdify_services takes it: never an argument of the tool."""
    actor = current_actor()
    return None if actor is None else ServiceActor(source=actor.source, name=actor.name)


def _uuid_argument(name: str, value: str | None) -> str | None:
    """A UUID string argument in its canonical form; ``invalid_request`` when it is not one."""
    if value is None:
        return None
    try:
        return str(UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ServiceError(problem("invalid_request", f"{name} must be a UUID", field=name)) from exc


def _time_argument(name: str, value: str | None) -> datetime | None:
    """An ISO-8601 time argument with its UTC offset; ``invalid_request`` when it is not one."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            problem("invalid_request", f"{name} must be an ISO-8601 time with its UTC offset", field=name)
        ) from exc
    if parsed.utcoffset() is None:
        raise ServiceError(problem("invalid_request", f"{name} needs its UTC offset", field=name))
    return parsed


async def _append_plan_activity(
    conn: asyncpg.Connection,
    *,
    plan_id: str,
    changes: list[dict[str, Any]],
    reason: str | None,
    trigger_id: str | None,
    planner_instance: str | None,
) -> None:
    """The one ``plan.write`` activity row of a set_plan or set_tunable, in its setpoint_plan transaction.

    Their contract is unchanged, so neither refuses for a missing actor: with none (auth mode off, a direct call)
    the row records (iris, iris), as their setpoint_plan rows do.
    """
    await activity_service.append(
        conn,
        _service_actor() or ServiceActor.iris(),
        action="plan.write",
        target=plan_id,
        detail={"changes": changes, "trigger_id": trigger_id, "planner_instance": planner_instance},
        reason=reason,
        experiment_id=await conn.fetchval(activity_service.ARMED_EXPERIMENT_SQL),
    )


# #387: outcome_kpi() fans its independent section fetches out concurrently,
# and asyncpg connections cannot multiplex queries — concurrency needs more
# connections. There is deliberately NO per-call connection burst: the extra
# connections come from this small shared pool, so the global fan-out is
# capped at max_size regardless of how hard a looping LLM caller hammers the
# tool (excess acquires queue instead of stacking DB backends). min_size=0
# plus a short inactive lifetime keeps the idle footprint at zero between
# bursts. Every pooled connection carries the same statement-timeout fence as
# _db().
_KPI_FANOUT_POOL_MAX_SIZE = 4
# Annotations quoted: schema-only CI imports this module with a lightweight
# asyncpg stub that has no Pool attribute (see _install_mcp_runtime_import_stubs).
_kpi_fanout_pool: "asyncpg.Pool | None" = None
_kpi_fanout_pool_lock = asyncio.Lock()


async def _kpi_fanout_pool_get() -> "asyncpg.Pool":
    global _kpi_fanout_pool
    if _kpi_fanout_pool is None:
        async with _kpi_fanout_pool_lock:
            if _kpi_fanout_pool is None:
                pool_options = {"init": _attest_mcp_runtime_connection} if _mcp_runtime_db_role_required() else {}
                _kpi_fanout_pool = await asyncpg.create_pool(
                    DB_DSN,
                    min_size=0,
                    max_size=_KPI_FANOUT_POOL_MAX_SIZE,
                    max_inactive_connection_lifetime=60.0,
                    server_settings={
                        "application_name": "verdify-mcp",
                        "statement_timeout": f"{MCP_DB_STATEMENT_TIMEOUT_MS}ms",
                    },
                    **pool_options,
                )
    return _kpi_fanout_pool


async def _fetch_kpi_action_scorecard(conn: asyncpg.Connection, sql: str, target_day: date, greenhouse_id: str):
    # SET LOCAL expires on both commit and rollback, including cancellation.
    # The following moisture/policy reads on this pooled connection retain the
    # normal 15 s session setting.
    async with conn.transaction(readonly=True):
        await conn.execute(f"SET LOCAL statement_timeout = '{KPI_ACTION_SCORECARD_TIMEOUT_MS}ms'")
        return await conn.fetch(sql, target_day, greenhouse_id)


def _custom_route(path: str, *, methods: list[str]):
    """Register a FastMCP route while keeping schema-only import stubs usable."""
    custom_route = getattr(mcp, "custom_route", None)
    if custom_route is None:
        return lambda func: func
    return custom_route(path, methods=methods)


@_custom_route("/readyz", methods=["GET"])
async def mcp_ready(_request):
    """Tool-level readiness used by Hermes and release acceptance.

    A listening TCP socket is insufficient: required tools can disappear from
    registration while the process remains healthy.  Readiness also proves a
    minimal DB round trip because every control tool depends on that store.
    """
    # Starlette is a runtime dependency of the MCP package, but schema-only CI
    # intentionally imports this module with lightweight MCP stubs.  Keep the
    # response dependency off that import path.
    from starlette.responses import JSONResponse

    # Readiness validates the complete server registry, not the current probe's
    # unauthenticated protocol view. Protocol tools/list is audience-filtered.
    registered = {tool.name for tool in await mcp.list_tools_unfiltered()}
    missing = sorted(HERMES_REQUIRED_TOOLS - registered)
    db_error: str | None = None
    conn: asyncpg.Connection | None = None
    try:
        conn = await _db()
        await conn.fetchval("SELECT 1")
    except Exception as exc:
        db_error = type(exc).__name__
    finally:
        if conn is not None:
            await conn.close()
    # #585: operators must be able to SEE the authorization rollout state
    # (off → log → enforce) on the same surface release acceptance already
    # reads. Names only — never token values. Enforce mode with an empty token
    # registry would fail-closed every tool call, so that misconfiguration
    # reports not-ready instead of serving a fully bricked tool surface.
    mode = auth_mode()
    token_registry, unrecognized_token_envs, duplicate_token_envs, invalid_token_envs = audience_token_configuration()
    auth_misconfigured = bool(unrecognized_token_envs or duplicate_token_envs or invalid_token_envs) or (
        mode == "enforce" and "iris" not in token_registry
    )
    # VD-59: named agents are reported by name only and never change `ready`.
    # The readiness probe's failureThreshold is 1, and one bad agent key must
    # not take Iris's tool surface out of service.
    agent_keys, rejected_agent_keys = agent_key_registry()
    ready = not missing and db_error is None and not auth_misconfigured
    return JSONResponse(
        {
            "ready": ready,
            "required_tools": sorted(HERMES_REQUIRED_TOOLS),
            "missing_tools": missing,
            "db": "ok" if db_error is None else "unavailable",
            "db_error_class": db_error,
            "auth_mode": mode,
            "auth_audiences_configured": sorted(token_registry),
            "auth_unrecognized_token_envs": unrecognized_token_envs,
            "auth_duplicate_token_envs": duplicate_token_envs,
            "auth_invalid_token_envs": invalid_token_envs,
            "auth_misconfigured": auth_misconfigured,
            "auth_agents_configured": sorted(agent_keys),
            "auth_agent_keys_rejected": rejected_agent_keys,
        },
        status_code=200 if ready else 503,
    )


async def _insert_plan_delivery_log(conn: asyncpg.Connection, result: dict) -> str | None:
    """Persist or refresh a send_to_iris result from MCP-triggered manual planning."""
    row = {
        "event_type": result["event_type"],
        "event_label": result.get("event_label"),
        "session_key": result.get("session_key"),
        "wake_mode": result.get("wake_mode"),
        "gateway_status": result.get("gateway_status"),
        "gateway_body": result.get("gateway_body"),
        "trigger_id": result.get("trigger_id"),
        "instance": result.get("instance"),
        "hermes_run_id": result.get("hermes_run_id"),
    }
    explicit_status = result.get("status")
    if explicit_status is None and result.get("delivered") is False and result.get("gateway_status") is not None:
        explicit_status = "delivery_failed"
    terminal_action = result.get("terminal_action")
    failure_class = result.get("failure_class")
    if explicit_status == "delivery_failed":
        terminal_action = terminal_action or "delivery_failed"
        failure_class = failure_class or "gateway_delivery_failed"
    if explicit_status is not None:
        row["status"] = explicit_status
    if terminal_action is not None:
        row["terminal_action"] = terminal_action
        row["failure_class"] = failure_class
    PlanDeliveryLogRow.model_validate(row)

    await conn.execute(
        """
        INSERT INTO plan_delivery_log AS pdl
          (event_type, event_label, session_key, wake_mode, gateway_status,
           gateway_body, trigger_id, instance, status, hermes_run_id,
           terminal_action, terminal_at, failure_class)
        VALUES ($1, $2, $3, $4, $5, $6, $7::uuid, $8, COALESCE($9, 'pending'), $10,
                $11, CASE WHEN $11::text IS NULL THEN NULL ELSE now() END, $12)
        ON CONFLICT (trigger_id) DO UPDATE
          SET event_type     = EXCLUDED.event_type,
              event_label    = EXCLUDED.event_label,
              session_key    = COALESCE(EXCLUDED.session_key, pdl.session_key),
              wake_mode      = COALESCE(EXCLUDED.wake_mode, pdl.wake_mode),
              gateway_status = EXCLUDED.gateway_status,
              gateway_body   = COALESCE(EXCLUDED.gateway_body, pdl.gateway_body),
              instance       = COALESCE(EXCLUDED.instance, pdl.instance),
              hermes_run_id  = COALESCE(EXCLUDED.hermes_run_id, pdl.hermes_run_id),
              terminal_action = COALESCE(pdl.terminal_action, EXCLUDED.terminal_action),
              terminal_at     = COALESCE(pdl.terminal_at, EXCLUDED.terminal_at),
              failure_class   = COALESCE(pdl.failure_class, EXCLUDED.failure_class),
              status         = CASE
                                 WHEN pdl.status IN (
                                     'acked', 'plan_written', 'action_completed',
                                     'neutral_fallback', 'wrong_action'
                                 ) THEN pdl.status
                                 ELSE EXCLUDED.status
                               END
        """,
        row["event_type"],
        row["event_label"],
        row["session_key"],
        row["wake_mode"],
        row["gateway_status"],
        row["gateway_body"],
        row["trigger_id"],
        row["instance"],
        explicit_status,
        row["hermes_run_id"],
        terminal_action,
        failure_class,
    )
    return explicit_status


# ═══════════════════════════════════════════════════════════════
# MONITORING TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def climate() -> str:
    """Get current greenhouse climate readings (temp, VPD, RH, dew point, zone sensors, outdoor)."""
    conn = await _db()
    try:
        row = await conn.fetchrow("""
            SELECT round(temp_avg::numeric,1) as temp_f,
                   round(vpd_avg::numeric,2) as vpd_kpa,
                   round(rh_avg::numeric,0) as rh_pct,
                   round(dew_point::numeric,1) as dew_point_f,
                   round((temp_avg - dew_point)::numeric,1) as dp_margin_f,
                   round(vpd_south::numeric,2) as vpd_south,
                   round(vpd_west::numeric,2) as vpd_west,
                   round(vpd_east::numeric,2) as vpd_east,
                   round(outdoor_temp_f::numeric,1) as outdoor_temp,
                   round(outdoor_rh_pct::numeric,0) as outdoor_rh,
                   round(lux::numeric,0) as lux,
                   round(solar_irradiance_w_m2::numeric,0) as solar_w,
                   round(solar_irradiance_w_m2::numeric,0) as solar_w_m2,
                   extract(epoch FROM now() - ts)::int as age_seconds
            FROM climate ORDER BY ts DESC LIMIT 1
        """)
        mode = await conn.fetchval(
            "SELECT value FROM system_state WHERE entity = 'greenhouse_state' ORDER BY ts DESC LIMIT 1"
        )
        # Sprint 23+: validate-on-emit through ClimateSnapshot. Schema drift
        # in the SELECT (e.g. a renamed column) fails here, not silently in
        # Iris's downstream parse.
        snap = ClimateSnapshot.model_validate({**dict(row), "mode": mode})
        return snap.model_dump_json()
    finally:
        await conn.close()


async def read_physical_crop_band_evidence(conn, day, greenhouse_id="vallery"):
    """Read only trusted-owner published revisions; invalid latest rows fail closed."""
    from verdify_schemas.physical_crop_band import PhysicalCropBandEvidence

    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL statement_timeout = '3000ms'")
            row = await conn.fetchrow(
                "SELECT reader_contract_version,day,greenhouse_id,served_at,revision_id,recorded_at,"
                "diagnostic,unavailable_reason FROM public.fn_physical_crop_band_evidence($1::date,$2::text)",
                day,
                greenhouse_id,
                timeout=3.5,
            )
        data = dict(row)
        if data["day"] != day or data["greenhouse_id"] != greenhouse_id:
            raise ValueError("physical evidence scope mismatch")
        if isinstance(data.get("diagnostic"), str):
            data["diagnostic"] = json.loads(data["diagnostic"])
        data["availability"] = "available" if data["unavailable_reason"] is None else "unavailable"
        return PhysicalCropBandEvidence.model_validate(data)
    except (asyncpg.QueryCanceledError, TimeoutError):
        reason = "db_statement_timeout"
    except (asyncpg.UndefinedFunctionError, asyncpg.UndefinedTableError, asyncpg.InsufficientPrivilegeError):
        reason = "reader_unavailable"
    except (ValidationError, ValueError, TypeError, KeyError, OverflowError):
        reason = "invalid_diagnostic"
    return PhysicalCropBandEvidence(day=day, greenhouse_id=greenhouse_id, unavailable_reason=reason)


@mcp.tool()
async def scorecard(target_date: str = "", include_realized_dryout: bool = False) -> str:
    """Get a daily scorecard; pass YYYY-MM-DD or omit for today.

    Inspect scorecard_contract_version and metric_semantics first. Only contract
    2 verifies that compliance_pct / temp_compliance_pct / vpd_compliance_pct are
    BINARY time-in-band shares. Earlier versions mislabeled graded credit under
    those keys. Since migration 290 (#1358) they grade each 5-minute house-average
    bucket against the band the controller served (band_house_* audit rows; the
    crop band where a row is missing); daily_band_compliance holds the minutes and
    the day/night split. They are not fixed-panel crop outcomes. Coverage is
    unverified; no center probe is measured. Binary stress hours are the minutes
    below or above that band / 60 and axes can overlap; graded_*_stress_h are
    separate deficit-integral diagnostics.

    compliance_v2_raw_pct, compliance_v2_attributable_pct, graded axis fields and
    compliance_v2_unachievable_frac describe the historical grading/feasibility
    per-zone model, not measured crop compliance. Controller-attributable credit refunds
    modeled unachievable misses; it must not hide physical exposure. The legacy
    center zone uses a house-average proxy, not a center measurement. Never widen
    served bands to improve credit or treat target-distance as a crop outcome.

    planner_score retains historical graded credit and resource rules: cost
    receives 20% weight only when conserved water and the whole-runtime energy
    model are scoring-eligible. Otherwise resource weight is zero. Inspect
    planner_score_resource_weight_pct and resource_terms_available before comparing
    scores. Null means unavailable, not zero or perfect. Composite outcome fields
    remain null until their physical evidence contract is implemented.

    observed_minute_evidence is a separately versioned, captured house-average
    diagnostic with per-axis/joint eligible counts, coverage and distance. Inspect
    availability, window and revision; snapshot freshness is not assessed. It is
    not continuous exposure, fixed-panel crop compliance, a confirmed consumed
    target or an experiment endpoint. Never substitute it for the scalar metrics.

    native_fixed_panel_route_evidence exposes actual source-route axis/joint
    eligible 15-minute bins, high/low distance and severity, worst source route
    and explicit future/undeclared/missing bins under a prospective target and
    exact firmware/collector binding. Availability route_measurement is sampled
    host-received callback evidence; physical serial, Modbus poll time and crop
    placement remain unverified. Partial windows are explicit. It is not a
    physical crop outcome, controller credit or experiment endpoint. Keep
    physical_crop_band_evidence unavailable until its own qualification exists.

    For ordinary Iris outcome review, explicitly call scorecard with a single
    target_date (YYYY-MM-DD) and include_realized_dryout=true for the prior
    completed solar night. target_date is both the scalar scorecard calendar day
    and the separate dry-out night START date; that night crosses local midnight.
    The opt-in adds one read under the existing DB budget, at most 500 episodes
    and 64 KiB of dry-out evidence. Ordinary calls do not run this query. Reuse
    the returned result within a planning cycle; do not poll or retry unavailable
    evidence. Inspect availability, evidence_status, confound_reasons, relay
    truth and observed AH/VPD response before using any disposition. Safety pass
    alone is not effectiveness; empty episodes or missing daylight evidence
    cannot prove zero admission, complete coverage or a causal crop benefit.

    Response is validated through verdify_schemas.ScorecardResponse. Partial days
    may contain null metrics; inspect evidence limitations before using a score."""
    conn = await _db()
    try:
        if target_date:
            d = datetime.strptime(target_date, "%Y-%m-%d").date()
        else:
            d = await conn.fetchval("SELECT (now() AT TIME ZONE 'America/Denver')::date")
        try:
            rows = await conn.fetch("SELECT * FROM fn_planner_scorecard($1::date)", d)
        except asyncpg.QueryCanceledError:
            return _json(
                {
                    "availability": "unavailable",
                    "reason": "db_statement_timeout",
                    "query_budget_ms": MCP_DB_STATEMENT_TIMEOUT_MS,
                    "guidance": "Do not infer zero performance; continue from fresh climate and forecast evidence.",
                }
            )
        try:
            sc = ScorecardResponse.from_metric_rows(rows)
        except ValidationError as e:
            return _json(
                {
                    "error": "ScorecardResponse validation failed — DB may have new metrics",
                    "details": json.loads(e.json()),
                    "raw": {r["metric"]: (float(r["value"]) if r["value"] is not None else None) for r in rows},
                }
            )
        sc.observed_minute_evidence = await read_observed_minute_evidence(conn, d)
        sc.physical_crop_band_evidence = await read_physical_crop_band_evidence(conn, d)
        sc.route_only_crop_band_evidence = await read_route_only_crop_band_evidence(conn, d)
        sc.native_fixed_panel_route_evidence = await read_native_fixed_panel_route_evidence(conn, d)
        if include_realized_dryout:
            sc.realized_solar_night_dryout = (
                await read_realized_dryout_evidence(conn, d, MCP_DB_STATEMENT_TIMEOUT_MS)
                if target_date
                else RealizedDryoutEvidence(unavailable_reason="explicit_night_date_required")
            )
        return sc.model_dump_json(by_alias=True)
    finally:
        await conn.close()


# #389: the transition-derived runtime contract lives in
# v_equipment_runtime_daily, but its single-day evaluation costs 11-40+ s in
# prod (O(days x transitions), migration-199/200 headers) — over the MCP
# statement fence — so outcome_kpi reads the migration-200 materialized
# snapshot (mv_equipment_runtime_daily, refreshed every 10 min) and reconciles:
# a completed local day must be complete in the snapshot, otherwise the read
# falls back to the live view so completed-day truth never silently degrades
# to a stale snapshot. Rows for the snapshot window bound come from
# migration 199.
_EQUIPMENT_RUNTIME_SNAPSHOT_WINDOW_DAYS = 45


def _cycle_day_status(target_day: date, today_local: date) -> str:
    """Deploy-gate day classification for the cycle/runtime axis (#389).

    Only completed local days are comparable across an OTA; the current local
    day is mid-flight (its transition counts are honest but partial) and a
    future-dated day has no evidence at all.
    """
    if target_day > today_local:
        return "future_date_excluded"
    if target_day == today_local:
        return "partial_day_excluded"
    return "complete"


def _cycle_snapshot_is_stale(cycle_rows, target_day: date, today_local: date) -> bool:
    """True when the materialized runtime snapshot cannot serve a completed day.

    A completed local day inside the migration-199 window must exist in the
    snapshot and be marked complete; a missing or still-partial row means the
    10-minute refresh has not run since local midnight (or the snapshot never
    covered the day), so the caller must re-read the live view instead of
    serving mid-day counts as completed-day truth. Partial (current) and
    future-dated targets never trigger the fallback — they are excluded from
    deploy-gate comparison regardless of snapshot freshness — and neither do
    days older than the operational window, where both surfaces are empty by
    design.
    """
    if target_day >= today_local:
        return False
    if target_day < today_local - timedelta(days=_EQUIPMENT_RUNTIME_SNAPSHOT_WINDOW_DAYS):
        return False
    if not cycle_rows:
        return True
    return any(not row["is_complete_day"] for row in cycle_rows)


@mcp.tool()
async def outcome_kpi(target_date: str = "") -> str:
    """Get ADR-0004 outcome KPIs for a day.

    This is the read-only outcome surface for floating-corridor control: served
    corridor compliance, VPD misses, transition-derived actuator cycles/runtime,
    dew margin, water
    use, availability-bearing DLI evidence, moisture-estimator decisions,
    fog/dehum ping-pong sequences,
    heat-dehum episodes, and per-action effectiveness from the daily climate-
    action scorecard. ADR-0004 means these are outcome and resource guardrails,
    not an instruction to target-hug while the crop is already inside the
    served corridor.

    Pinched corridor compliance, DIF, and solar-phase buckets are computed on
    demand from one-minute climate samples plus the active setpoint/readback
    state. Moisture-estimator buckets are computed from
    climate_action_log.source_system_state->climate_moisture_exchange when the
    OTA/deploy path has produced rows; #327 makes that context first-class:
    per-(action, reason) buckets now include the #410 held-temp fields
    (vent_held_vpd_gain_kpa, hold_required), the selected/expected VPD gain,
    and outdoor age, and vpd_policy carries episode counters by estimator
    reason (episodes_by_mx_reason; pre-#385 rows bucket as estimator_absent).
    Migration 187's v_moisture_estimator_telemetry view is the equivalent
    typed SQL surface.  Raw equipment transitions, not firmware daily counters,
    are the cycle/runtime authority; the counters remain diagnostic context.
    Cycle/runtime rows are read from the migration-200 materialized snapshot
    (mv_equipment_runtime_daily, refreshed every 10 minutes) with a live-view
    fallback whenever the snapshot cannot serve a completed day; only
    completed local days participate in deploy-gate cycle comparison —
    the current (partial) day and future-dated targets are explicitly
    excluded (vpd_policy.cycle_source.deploy_gate).
    Realized solar-night dry-out episodes distinguish effective, ineffective,
    blocked, and insufficient-evidence outcomes. Pass date as YYYY-MM-DD or
    omit for today.

    Single-day, cache-friendly surface: one call covers exactly one
    Denver-local day (multi-day ranges are not accepted), and completed days
    are stable reads — identical inputs return identical content — so a
    looping LLM caller (e.g. the planner) should reuse prior responses for
    finished dates instead of re-polling. The independent section fetches run
    concurrently on a small bounded connection pool and the shared 1-minute
    resolved-samples scan is computed once, so repeated calls queue on the
    pool instead of fanning out unbounded database load."""
    greenhouse_id = "vallery"
    conn = await _db()
    try:
        # today_local comes from the DB clock (the same clock that stamps
        # equipment_state and the runtime views) so the #389 partial/future
        # classification below can never disagree with the SQL surfaces.
        today_local = await conn.fetchval("SELECT (now() AT TIME ZONE 'America/Denver')::date")
        if target_date:
            try:
                d = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                return _json({"error": "target_date must be YYYY-MM-DD"})
        else:
            d = today_local

        summary_sql = """
            SELECT date, greenhouse_id,
                   compliance_v2_attributable_pct,
                   compliance_v2_raw_pct,
                   compliance_v2_unachievable_frac,
                   compliance_pct,
                   temp_compliance_pct,
                   vpd_compliance_pct,
                   graded_temp_compliance_pct,
                   graded_vpd_compliance_pct,
                   feasibility_unknown_min,
                   stress_hours_vpd_high,
                   stress_hours_vpd_low,
                   graded_stress_hours_vpd_high,
                   graded_stress_hours_vpd_low,
                   cycles_fan1,
                   cycles_fan2,
                   cycles_heat1,
                   cycles_heat2,
                   cycles_fog,
                   cycles_vent,
                   cycles_dehum,
                   cycles_safety_dehum,
                   cycles_mister_south,
                   cycles_mister_west,
                   cycles_mister_center,
                   cycles_grow_light,
                   runtime_fan1_min,
                   runtime_fan2_min,
                   runtime_heat1_min,
                   runtime_heat2_min,
                   runtime_fog_min,
                   runtime_vent_min,
                   runtime_mister_south_h,
                   runtime_mister_west_h,
                   runtime_mister_center_h,
                   runtime_grow_light_min,
                   water_used_gal,
                   mister_water_gal,
                   irrigation_water_gal,
                   fertigation_water_gal,
                   min_dp_margin_f,
                   dp_risk_hours,
                   kwh_estimated,
                   therms_estimated,
                   cost_electric,
                   cost_gas,
                   cost_water,
                   cost_total
            FROM daily_summary
            WHERE date = $1::date AND greenhouse_id = $2
            """
        dli_sql = """
            SELECT crop_dli_mol_m2_day AS value_mol_m2_day,
                   availability,
                   unavailable_reason,
                   provenance,
                   validity_revision,
                   valid_from,
                   valid_to
            FROM v_dli_daily
            WHERE date = $1::date AND greenhouse_id = $2
            """
        dli_validity_sql = """
                SELECT NULL::double precision AS value_mol_m2_day,
                       COALESCE(availability, 'unavailable') AS availability,
                       COALESCE(unavailable_reason, 'validity_contract_missing') AS unavailable_reason,
                       COALESCE(provenance, 'unknown_unvalidated_source') AS provenance,
                       COALESCE(validity_revision, 'missing') AS validity_revision,
                       COALESCE(valid_from, '2024-01-01 00:00:00+00'::timestamptz) AS valid_from,
                       valid_to
                FROM (SELECT 1) anchor
                LEFT JOIN LATERAL fn_dli_validity(
                    ($1::date::timestamp + interval '12 hours') AT TIME ZONE 'America/Denver',
                    $2
                ) ON true
                """
        water_sql = """
            SELECT *
            FROM v_water_attribution_daily
            WHERE date = $1::date AND greenhouse_id = $2
            """
        energy_sql = """
            SELECT *
            FROM v_energy_estimate_reconciliation
            WHERE date = $1::date AND greenhouse_id = $2
            """
        # #389: one shared column list, two sources. The materialized snapshot
        # (migration 200, refreshed every 10 min) is the fast path; the live
        # view is the reconciliation fallback when the snapshot cannot serve a
        # completed day (see _cycle_snapshot_is_stale). Both carry the
        # identical migration-190/199 transition-derived contract.
        cycles_columns = """
            SELECT equipment,
                   on_minutes::double precision AS on_minutes,
                   starts,
                   cycles_under_1m,
                   cycles_1m_to_5m,
                   short_cycles_under_5m,
                   cycles_5m_to_15m,
                   cycles_15m_plus,
                   open_pulses_at_cutoff,
                   peak_transitions_per_hour,
                   is_complete_day,
                   start_state_known,
                   open_at_end,
                   is_deploy_gate_eligible,
                   quality,
                   quality_flags,
                   raw_event_rows,
                   normalized_transition_count,
                   same_timestamp_duplicate_rows,
                   redundant_state_rows,
                   conflicting_timestamp_count
            """
        cycles_snapshot_sql = (
            cycles_columns
            + """
            FROM mv_equipment_runtime_daily
            WHERE day = $1::date AND greenhouse_id = $2
            ORDER BY equipment
            """
        )
        cycles_live_sql = (
            cycles_columns
            + """
            FROM v_equipment_runtime_daily
            WHERE day = $1::date AND greenhouse_id = $2
            ORDER BY equipment
            """
        )
        dryout_sql = """
            SELECT *
            FROM fn_realized_solar_night_dryout($1::date, $1::date, $2)
            ORDER BY episode_started_at
            """
        # #498: the migration-202 single-day function, not the whole-window
        # view — v_climate_action_daily_scorecard evaluates the effectiveness
        # fan-out (fn_equip_at/fn_setpoint_at per action row) at ~6 min of DB
        # CPU per call; the function is byte-identical for one Denver-local
        # date and prunes to that day's chunks (~5 s measured in prod).
        actions_sql = """
            SELECT climate_action,
                   decisions,
                   avg_abs_temp_error_before_f,
                   avg_abs_vpd_error_before_kpa,
                   avg_temp_abs_error_delta_15m_f,
                   avg_vpd_abs_error_delta_15m_kpa,
                   avg_wet_relay_duty_pct,
                   avg_vent_fan_duty_pct,
                   mister_water_delta_gal,
                   wet_blocked_decisions,
                   fog_blocked_decisions
            FROM fn_climate_action_daily_scorecard($1::date)
            WHERE greenhouse_id = $2
            ORDER BY decisions DESC, climate_action
            """
        pinched_phase_sql = """
            WITH bounds AS (
                SELECT
                    ($1::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
                    (($1::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
            ),
            -- #387: ONE 1-minute climate scan and ONE 6-way LATERAL
            -- setpoint/band resolution feed BOTH the pinched-corridor and
            -- solar-phase sections (each previously ran this fan-out
            -- independently). resolved_samples is referenced twice below, so
            -- PostgreSQL materializes it once. Row-set fidelity: the pinched
            -- section reads the unprefixed bucket averages (rows with
            -- temp/vpd present); the solar-phase section additionally
            -- requires solar_phase IS NOT NULL per row, which the FILTERed
            -- ph_* averages and the ph_sample_rows guard reproduce exactly.
            samples AS (
                SELECT
                    time_bucket('1 minute', c.ts) AS bucket,
                    avg(c.temp_avg)::double precision AS temp_avg,
                    avg(c.vpd_avg)::double precision AS vpd_avg,
                    avg(c.house_temp_target_f)::double precision AS temp_target_f,
                    avg(c.house_vpd_target)::double precision AS vpd_target_kpa,
                    avg(c.solar_phase)::double precision AS solar_phase,
                    count(c.solar_phase)::int AS ph_sample_rows,
                    (avg(c.temp_avg) FILTER (WHERE c.solar_phase IS NOT NULL)
                        )::double precision AS ph_temp_avg,
                    (avg(c.vpd_avg) FILTER (WHERE c.solar_phase IS NOT NULL)
                        )::double precision AS ph_vpd_avg,
                    (avg(c.dew_point) FILTER (WHERE c.solar_phase IS NOT NULL)
                        )::double precision AS ph_dew_point,
                    (avg(c.solar_irradiance_w_m2) FILTER (WHERE c.solar_phase IS NOT NULL)
                        )::double precision AS ph_solar_w_m2,
                    (avg(c.house_temp_target_f) FILTER (WHERE c.solar_phase IS NOT NULL)
                        )::double precision AS ph_temp_target_f,
                    (avg(c.house_vpd_target) FILTER (WHERE c.solar_phase IS NOT NULL)
                        )::double precision AS ph_vpd_target_kpa
                FROM climate c
                CROSS JOIN bounds b
                WHERE c.greenhouse_id = $2
                  AND c.ts >= b.start_ts
                  AND c.ts < b.end_ts
                  AND c.temp_avg IS NOT NULL
                  AND c.vpd_avg IS NOT NULL
                GROUP BY 1
            ),
            resolved_samples AS (
                SELECT
                    s.*,
                    COALESCE(temp_low.value, band.temp_low) AS temp_low_f,
                    COALESCE(temp_high.value, band.temp_high) AS temp_high_f,
                    COALESCE(vpd_low.value, house.house_vpd_low) AS vpd_low_kpa,
                    COALESCE(vpd_high.value, house.house_vpd_high) AS vpd_high_kpa,
                    GREATEST(
                        0.0,
                        LEAST(
                            1.0,
                            COALESCE(btf_readback.value, btf_change.value, 0.0)
                        )
                    ) AS band_track_fraction,
                    btf_readback.value IS NOT NULL AS has_fraction_readback
                FROM samples s
                CROSS JOIN LATERAL fn_band_setpoints(s.bucket) AS band
                CROSS JOIN LATERAL fn_house_vpd_control_band(s.bucket) AS house
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2 AND parameter = 'temp_low' AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) temp_low ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2 AND parameter = 'temp_high' AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) temp_high ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2 AND parameter = 'vpd_low' AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) vpd_low ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2 AND parameter = 'vpd_high' AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) vpd_high ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2
                      AND parameter = 'band_track_fraction'
                      AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) btf_readback ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_changes
                    WHERE greenhouse_id = $2
                      AND parameter = 'band_track_fraction'
                      AND ts <= s.bucket
                      AND (expired_at IS NULL OR expired_at > s.bucket)
                    ORDER BY ts DESC LIMIT 1
                ) btf_change ON true
            ),
            pinched_eligible AS (
                SELECT
                    *,
                    temp_low_f + band_track_fraction
                        * (LEAST(GREATEST(temp_target_f, temp_low_f), temp_high_f) - temp_low_f)
                        AS pinched_temp_low_f,
                    temp_high_f - band_track_fraction
                        * (temp_high_f - LEAST(GREATEST(temp_target_f, temp_low_f), temp_high_f))
                        AS pinched_temp_high_f,
                    vpd_low_kpa + band_track_fraction
                        * (LEAST(GREATEST(vpd_target_kpa, vpd_low_kpa), vpd_high_kpa) - vpd_low_kpa)
                        AS pinched_vpd_low_kpa,
                    vpd_high_kpa - band_track_fraction
                        * (vpd_high_kpa - LEAST(GREATEST(vpd_target_kpa, vpd_low_kpa), vpd_high_kpa))
                        AS pinched_vpd_high_kpa
                FROM resolved_samples
                WHERE temp_target_f IS NOT NULL
                  AND vpd_target_kpa IS NOT NULL
                  AND temp_low_f IS NOT NULL
                  AND temp_high_f IS NOT NULL
                  AND vpd_low_kpa IS NOT NULL
                  AND vpd_high_kpa IS NOT NULL
            ),
            pinched_scored AS (
                SELECT
                    *,
                    GREATEST(pinched_temp_low_f - temp_avg, temp_avg - pinched_temp_high_f, 0.0)
                        AS temp_pinched_miss_f,
                    GREATEST(pinched_vpd_low_kpa - vpd_avg, vpd_avg - pinched_vpd_high_kpa, 0.0)
                        AS vpd_pinched_miss_kpa,
                    temp_avg BETWEEN pinched_temp_low_f AND pinched_temp_high_f AS temp_pinched_in_band,
                    vpd_avg BETWEEN pinched_vpd_low_kpa AND pinched_vpd_high_kpa AS vpd_pinched_in_band
                FROM pinched_eligible
            ),
            pinched AS (
            SELECT
                count(*)::int AS sample_min,
                count(*) FILTER (WHERE has_fraction_readback)::int AS samples_with_fraction_readback,
                round(avg(band_track_fraction)::numeric, 3)::double precision
                    AS avg_band_track_fraction,
                round(min(band_track_fraction)::numeric, 3)::double precision
                    AS min_band_track_fraction,
                round(max(band_track_fraction)::numeric, 3)::double precision
                    AS max_band_track_fraction,
                round((100.0 * count(*) FILTER (WHERE temp_pinched_in_band)
                    / NULLIF(count(*), 0))::numeric, 2)::double precision AS temp_pct,
                round((100.0 * count(*) FILTER (WHERE vpd_pinched_in_band)
                    / NULLIF(count(*), 0))::numeric, 2)::double precision AS vpd_pct,
                round((100.0 * count(*) FILTER (WHERE temp_pinched_in_band AND vpd_pinched_in_band)
                    / NULLIF(count(*), 0))::numeric, 2)::double precision AS both_pct,
                round((100.0 * count(*) FILTER (
                        WHERE solar_phase < 2.0 AND temp_pinched_in_band AND vpd_pinched_in_band
                    ) / NULLIF(count(*) FILTER (WHERE solar_phase < 2.0), 0))::numeric, 2)::double precision
                    AS day_pct,
                round((100.0 * count(*) FILTER (
                        WHERE solar_phase >= 2.0 AND temp_pinched_in_band AND vpd_pinched_in_band
                    ) / NULLIF(count(*) FILTER (WHERE solar_phase >= 2.0), 0))::numeric, 2)::double precision
                    AS night_pct,
                round((percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY temp_pinched_miss_f
                ))::numeric, 2)::double precision AS p95_temp_miss_f,
                round((percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY vpd_pinched_miss_kpa
                ))::numeric, 3)::double precision AS p95_vpd_miss_kpa
            FROM pinched_scored
            ),
            phase_resolved AS (
                SELECT
                    s.bucket,
                    s.ph_temp_avg AS temp_avg,
                    s.ph_vpd_avg AS vpd_avg,
                    s.ph_dew_point AS dew_point,
                    s.ph_solar_w_m2 AS solar_w_m2,
                    s.solar_phase,
                    s.ph_temp_target_f AS temp_target_f,
                    s.ph_vpd_target_kpa AS vpd_target_kpa,
                    CASE
                        WHEN s.solar_phase < 1.0 THEN 'sunrise_to_noon'
                        WHEN s.solar_phase < 2.0 THEN 'noon_to_sunset'
                        WHEN s.solar_phase < 3.0 THEN 'sunset_to_midnight'
                        ELSE 'midnight_to_sunrise'
                    END AS phase_bucket,
                    CASE
                        WHEN s.solar_phase < 1.0 THEN 0
                        WHEN s.solar_phase < 2.0 THEN 1
                        WHEN s.solar_phase < 3.0 THEN 2
                        ELSE 3
                    END AS phase_order,
                    s.temp_low_f,
                    s.temp_high_f,
                    s.vpd_low_kpa,
                    s.vpd_high_kpa,
                    s.band_track_fraction
                FROM resolved_samples s
                WHERE s.ph_sample_rows > 0
            ),
            phase_scored AS (
                SELECT
                    *,
                    temp_low_f + band_track_fraction
                        * (LEAST(GREATEST(temp_target_f, temp_low_f), temp_high_f) - temp_low_f)
                        AS pinched_temp_low_f,
                    temp_high_f - band_track_fraction
                        * (temp_high_f - LEAST(GREATEST(temp_target_f, temp_low_f), temp_high_f))
                        AS pinched_temp_high_f,
                    vpd_low_kpa + band_track_fraction
                        * (LEAST(GREATEST(vpd_target_kpa, vpd_low_kpa), vpd_high_kpa) - vpd_low_kpa)
                        AS pinched_vpd_low_kpa,
                    vpd_high_kpa - band_track_fraction
                        * (vpd_high_kpa - LEAST(GREATEST(vpd_target_kpa, vpd_low_kpa), vpd_high_kpa))
                        AS pinched_vpd_high_kpa
                FROM phase_resolved
                WHERE temp_target_f IS NOT NULL
                  AND vpd_target_kpa IS NOT NULL
                  AND temp_low_f IS NOT NULL
                  AND temp_high_f IS NOT NULL
                  AND vpd_low_kpa IS NOT NULL
                  AND vpd_high_kpa IS NOT NULL
            ),
            phase AS (
                SELECT
                    phase_bucket,
                    phase_order,
                count(*)::int AS sample_min,
                round(min(solar_phase)::numeric, 2)::double precision AS phase_min,
                round(max(solar_phase)::numeric, 2)::double precision AS phase_max,
                round(avg(temp_avg)::numeric, 2)::double precision AS temp_avg_f,
                round(avg(vpd_avg)::numeric, 3)::double precision AS vpd_avg_kpa,
                round(min(temp_avg - dew_point)::numeric, 2)::double precision AS min_dp_margin_f,
                round(avg(solar_w_m2)::numeric, 1)::double precision AS avg_solar_w_m2,
                round((100.0 * count(*) FILTER (
                        WHERE temp_avg BETWEEN temp_low_f AND temp_high_f
                          AND vpd_avg BETWEEN vpd_low_kpa AND vpd_high_kpa
                    ) / NULLIF(count(*), 0))::numeric, 2)::double precision AS served_both_pct,
                round((100.0 * count(*) FILTER (
                        WHERE temp_avg BETWEEN pinched_temp_low_f AND pinched_temp_high_f
                          AND vpd_avg BETWEEN pinched_vpd_low_kpa AND pinched_vpd_high_kpa
                    ) / NULLIF(count(*), 0))::numeric, 2)::double precision AS pinched_both_pct,
                round((100.0 * count(*) FILTER (
                        WHERE vpd_avg > vpd_high_kpa
                    ) / NULLIF(count(*), 0))::numeric, 2)::double precision AS vpd_high_miss_pct,
                round((100.0 * count(*) FILTER (
                        WHERE vpd_avg < vpd_low_kpa
                    ) / NULLIF(count(*), 0))::numeric, 2)::double precision AS vpd_low_miss_pct
                FROM phase_scored
                GROUP BY phase_order, phase_bucket
            )
            SELECT
                p.sample_min,
                p.samples_with_fraction_readback,
                p.avg_band_track_fraction,
                p.min_band_track_fraction,
                p.max_band_track_fraction,
                p.temp_pct,
                p.vpd_pct,
                p.both_pct,
                p.day_pct,
                p.night_pct,
                p.p95_temp_miss_f,
                p.p95_vpd_miss_kpa,
                ph.phase_bucket AS ph_phase_bucket,
                ph.sample_min AS ph_sample_min,
                ph.phase_min AS ph_phase_min,
                ph.phase_max AS ph_phase_max,
                ph.temp_avg_f AS ph_temp_avg_f,
                ph.vpd_avg_kpa AS ph_vpd_avg_kpa,
                ph.min_dp_margin_f AS ph_min_dp_margin_f,
                ph.avg_solar_w_m2 AS ph_avg_solar_w_m2,
                ph.served_both_pct AS ph_served_both_pct,
                ph.pinched_both_pct AS ph_pinched_both_pct,
                ph.vpd_high_miss_pct AS ph_vpd_high_miss_pct,
                ph.vpd_low_miss_pct AS ph_vpd_low_miss_pct
            FROM pinched p
            LEFT JOIN phase ph ON true
            ORDER BY ph.phase_order
            """
        dif_sql = """
            WITH bounds AS (
                SELECT
                    ($1::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
                    (($1::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
            ),
            samples AS (
                SELECT
                    time_bucket('1 minute', c.ts) AS bucket,
                    avg(c.temp_avg)::double precision AS temp_avg,
                    avg(c.solar_phase)::double precision AS solar_phase
                FROM climate c
                CROSS JOIN bounds b
                WHERE c.greenhouse_id = $2
                  AND c.ts >= b.start_ts
                  AND c.ts < b.end_ts
                  AND c.temp_avg IS NOT NULL
                  AND c.solar_phase IS NOT NULL
                GROUP BY 1
            )
            SELECT
                count(*) FILTER (WHERE solar_phase < 2.0)::int AS day_sample_min,
                count(*) FILTER (WHERE solar_phase >= 2.0)::int AS night_sample_min,
                round((avg(temp_avg) FILTER (WHERE solar_phase < 2.0))::numeric, 2)::double precision
                    AS day_temp_avg_f,
                round((avg(temp_avg) FILTER (WHERE solar_phase >= 2.0))::numeric, 2)::double precision
                    AS night_temp_avg_f,
                round((
                    avg(temp_avg) FILTER (WHERE solar_phase < 2.0)
                    - avg(temp_avg) FILTER (WHERE solar_phase >= 2.0)
                )::numeric, 2)::double precision AS day_night_temp_delta_f
            FROM samples
            """
        moisture_sql = """
            WITH bounds AS (
                SELECT
                    ($1::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
                    (($1::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
            ),
            estimator AS (
                SELECT
                    source_system_state -> 'climate_moisture_exchange' AS mx
                FROM climate_action_log
                CROSS JOIN bounds b
                WHERE greenhouse_id = $2
                  AND ts >= b.start_ts
                  AND ts < b.end_ts
                  AND jsonb_typeof(source_system_state -> 'climate_moisture_exchange') = 'object'
            ),
            parsed AS (
                SELECT
                    COALESCE(NULLIF(mx ->> 'action', ''), 'unknown') AS action,
                    COALESCE(NULLIF(mx ->> 'reason', ''), 'unknown') AS reason,
                    NULLIF(mx ->> 'vent_vpd_gain_kpa', '')::double precision
                        AS vent_vpd_gain_kpa,
                    NULLIF(mx ->> 'heat_vpd_gain_kpa', '')::double precision
                        AS heat_vpd_gain_kpa,
                    -- #327/#410 fields (settled names; NULL from pre-#410
                    -- emitters). typeof-guarded like migration 187's
                    -- v_moisture_estimator_telemetry so raw/odd payloads
                    -- degrade to NULL instead of erroring.
                    CASE WHEN jsonb_typeof(mx -> 'vent_held_vpd_gain_kpa') = 'number'
                        THEN (mx ->> 'vent_held_vpd_gain_kpa')::double precision
                    END AS vent_held_vpd_gain_kpa,
                    CASE WHEN mx ->> 'hold_required' IN ('true', 'false')
                        THEN (mx ->> 'hold_required')::boolean
                    END AS hold_required,
                    CASE WHEN jsonb_typeof(mx -> 'expected_vpd_gain_kpa') = 'number'
                        THEN (mx ->> 'expected_vpd_gain_kpa')::double precision
                    END AS expected_vpd_gain_kpa_raw,
                    COALESCE(
                        CASE WHEN jsonb_typeof(mx -> 'outdoor_age_s') = 'number'
                            THEN (mx ->> 'outdoor_age_s')::double precision
                        END,
                        CASE WHEN jsonb_typeof(mx -> 'outdoor_data_age_s') = 'number'
                            THEN (mx ->> 'outdoor_data_age_s')::double precision
                        END
                    ) AS outdoor_age_s,
                    CASE WHEN mx ->> 'outdoor_fresh' IN ('true', 'false')
                        THEN (mx ->> 'outdoor_fresh')::boolean
                    END AS outdoor_fresh,
                    CASE WHEN mx ->> 'vent_overcools' IN ('true', 'false')
                        THEN (mx ->> 'vent_overcools')::boolean
                    END AS vent_overcools,
                    CASE WHEN mx ->> 'heat_assist_corun' IN ('true', 'false')
                        THEN (mx ->> 'heat_assist_corun')::boolean
                    END AS heat_assist_corun,
                    CASE WHEN mx ->> 'heat_assist_active' IN ('true', 'false')
                        THEN (mx ->> 'heat_assist_active')::boolean
                    END AS heat_assist_active,
                    NULLIF(mx ->> 'heat_assist_timer_s', '')::double precision
                        AS heat_assist_timer_s
                FROM estimator
            ),
            enriched AS (
                -- Selected/expected gain: explicit emitter value if present,
                -- else the selected path's own projection (held-temp gain for
                -- the #410 vent_plus_heat_hold / hold_required co-run).
                SELECT parsed.*,
                    COALESCE(
                        expected_vpd_gain_kpa_raw,
                        CASE
                            WHEN reason = 'vent_plus_heat_hold'
                                 OR COALESCE(hold_required, false)
                                THEN COALESCE(vent_held_vpd_gain_kpa, vent_vpd_gain_kpa)
                            WHEN action IN ('vent_dehum', 'vent_humidify')
                                THEN vent_vpd_gain_kpa
                            WHEN action = 'heat_assist' THEN heat_vpd_gain_kpa
                        END
                    ) AS expected_vpd_gain_kpa
                FROM parsed
            )
            SELECT
                action,
                reason,
                count(*)::int AS decisions,
                round(avg(vent_vpd_gain_kpa)::numeric, 3)::double precision
                    AS avg_vent_vpd_gain_kpa,
                round(avg(heat_vpd_gain_kpa)::numeric, 3)::double precision
                    AS avg_heat_vpd_gain_kpa,
                round(avg(vent_held_vpd_gain_kpa)::numeric, 3)::double precision
                    AS avg_vent_held_vpd_gain_kpa,
                round(avg(expected_vpd_gain_kpa)::numeric, 3)::double precision
                    AS avg_expected_vpd_gain_kpa,
                count(*) FILTER (WHERE hold_required)::int AS hold_required_decisions,
                count(*) FILTER (WHERE outdoor_fresh)::int AS outdoor_fresh_decisions,
                round(avg(outdoor_age_s)::numeric, 0)::double precision
                    AS avg_outdoor_age_s,
                count(*) FILTER (WHERE vent_overcools)::int AS vent_overcool_decisions,
                count(*) FILTER (WHERE heat_assist_corun)::int AS heat_assist_corun_decisions,
                count(*) FILTER (WHERE heat_assist_active)::int AS heat_assist_active_decisions,
                round(avg(heat_assist_timer_s)::numeric, 0)::double precision
                    AS avg_heat_assist_timer_s
            FROM enriched
            GROUP BY action, reason
            ORDER BY decisions DESC, action, reason
            """
        vpd_policy_sql = """
            WITH bounds AS (
                SELECT
                    ($1::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
                    (($1::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
            ),
            ordered AS (
                SELECT
                    l.ts,
                    l.climate_action,
                    l.priority_axis,
                    l.candidate_summary,
                    l.source_system_state,
                    lag(l.climate_action) OVER (ORDER BY l.ts) AS prev_action
                FROM climate_action_log l
                CROSS JOIN bounds b
                WHERE l.greenhouse_id = $2
                  AND l.ts >= b.start_ts
                  AND l.ts < b.end_ts
            ),
            tagged AS (
                SELECT
                    *,
                    sum(CASE WHEN prev_action IS DISTINCT FROM climate_action THEN 1 ELSE 0 END)
                        OVER (ORDER BY ts ROWS UNBOUNDED PRECEDING) AS episode_id
                FROM ordered
            ),
            episodes AS (
                SELECT
                    episode_id,
                    min(ts) AS started_at,
                    max(ts) AS ended_at,
                    climate_action,
                    count(*)::int AS sample_count,
                    bool_or(priority_axis = 'vpd') AS vpd_priority,
                    bool_or(COALESCE(candidate_summary, '') ILIKE '%heat-assist dehum%')
                        AS heat_dehum_summary,
                    bool_or(
                        source_system_state -> 'climate_moisture_exchange' ->> 'action'
                            = 'heat_assist'
                    ) AS mx_heat_assist,
                    bool_or(
                        source_system_state -> 'climate_moisture_exchange' ->> 'heat_assist_active'
                            = 'true'
                    ) AS mx_heat_assist_active
                FROM tagged
                GROUP BY episode_id, climate_action
            ),
            sequenced AS (
                SELECT
                    *,
                    lag(climate_action) OVER (ORDER BY started_at) AS prev_episode_action,
                    lag(ended_at) OVER (ORDER BY started_at) AS prev_episode_ended_at
                FROM episodes
            ),
            classified AS (
                SELECT
                    *,
                    climate_action IN (
                        'SEALED_HUMIDIFY',
                        'SEALED_FOG',
                        'VENT_COOL_MIST_ASSIST',
                        'VENT_COOL_FOG_ASSIST'
                    ) AS is_wetting,
                    climate_action = 'DEHUM_VENT' AS is_dehum,
                    climate_action = 'HEAT'
                        AND (vpd_priority OR heat_dehum_summary OR mx_heat_assist OR mx_heat_assist_active)
                        AS is_heat_dehum,
                    started_at - prev_episode_ended_at AS prev_gap
                FROM sequenced
            )
            SELECT
                count(*)::int AS total_episodes,
                COALESCE(sum(sample_count), 0)::int AS total_samples,
                count(*) FILTER (WHERE is_wetting)::int AS wetting_episodes,
                count(*) FILTER (WHERE is_dehum)::int AS vent_dehum_episodes,
                count(*) FILTER (WHERE is_heat_dehum)::int AS heat_dehum_episodes,
                count(*) FILTER (
                    WHERE is_dehum
                      AND prev_episode_action IN (
                          'SEALED_HUMIDIFY',
                          'SEALED_FOG',
                          'VENT_COOL_MIST_ASSIST',
                          'VENT_COOL_FOG_ASSIST'
                      )
                      AND prev_gap <= interval '30 minutes'
                )::int AS wet_to_dehum_episodes_30m,
                count(*) FILTER (
                    WHERE is_wetting
                      AND prev_episode_action = 'DEHUM_VENT'
                      AND prev_gap <= interval '30 minutes'
                )::int AS dehum_to_wet_episodes_30m,
                round((
                    avg(extract(epoch FROM prev_gap) / 60.0) FILTER (
                        WHERE is_dehum
                          AND prev_episode_action IN (
                              'SEALED_HUMIDIFY',
                              'SEALED_FOG',
                              'VENT_COOL_MIST_ASSIST',
                              'VENT_COOL_FOG_ASSIST'
                          )
                          AND prev_gap <= interval '30 minutes'
                    )
                )::numeric, 1)::double precision AS avg_wet_to_dehum_gap_min,
                round((
                    avg(extract(epoch FROM prev_gap) / 60.0) FILTER (
                        WHERE is_wetting
                          AND prev_episode_action = 'DEHUM_VENT'
                          AND prev_gap <= interval '30 minutes'
                    )
                )::numeric, 1)::double precision AS avg_dehum_to_wet_gap_min
            FROM classified
            """
        # #327: VPD-policy episode counters by estimator reason (mx_reason).
        # One row per modal estimator reason across the day's action episodes;
        # pre-#385 rows (no parsed object) land in 'estimator_absent' so the
        # counters stay meaningful across the fw 995c9b3 -> #385 -> #410
        # rollout. This is the #371 grading surface for "did the cycle take
        # vent_plus_heat_hold or heat_assist?".
        vpd_policy_reason_sql = """
            WITH bounds AS (
                SELECT
                    ($1::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
                    (($1::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
            ),
            ordered AS (
                SELECT
                    l.ts,
                    l.climate_action,
                    l.priority_axis,
                    lag(l.climate_action) OVER (ORDER BY l.ts) AS prev_action,
                    CASE WHEN jsonb_typeof(
                             l.source_system_state -> 'climate_moisture_exchange'
                         ) = 'object'
                        THEN NULLIF(
                            l.source_system_state -> 'climate_moisture_exchange' ->> 'reason',
                            ''
                        )
                    END AS mx_reason
                FROM climate_action_log l
                CROSS JOIN bounds b
                WHERE l.greenhouse_id = $2
                  AND l.ts >= b.start_ts
                  AND l.ts < b.end_ts
            ),
            tagged AS (
                SELECT
                    *,
                    sum(CASE WHEN prev_action IS DISTINCT FROM climate_action THEN 1 ELSE 0 END)
                        OVER (ORDER BY ts ROWS UNBOUNDED PRECEDING) AS episode_id
                FROM ordered
            ),
            episodes AS (
                SELECT
                    episode_id,
                    climate_action,
                    count(*)::int AS sample_count,
                    bool_or(priority_axis = 'vpd') AS vpd_priority,
                    COALESCE(
                        mode() WITHIN GROUP (ORDER BY mx_reason),
                        'estimator_absent'
                    ) AS mx_reason
                FROM tagged
                GROUP BY episode_id, climate_action
            )
            SELECT
                mx_reason,
                count(*)::int AS episodes,
                COALESCE(sum(sample_count), 0)::int AS samples,
                count(*) FILTER (WHERE climate_action = 'DEHUM_VENT')::int
                    AS vent_dehum_episodes,
                count(*) FILTER (WHERE climate_action = 'HEAT' AND vpd_priority)::int
                    AS heat_dehum_episodes,
                count(*) FILTER (
                    WHERE climate_action IN (
                        'SEALED_HUMIDIFY',
                        'SEALED_FOG',
                        'VENT_COOL_MIST_ASSIST',
                        'VENT_COOL_FOG_ASSIST'
                    )
                )::int AS wetting_episodes
            FROM episodes
            GROUP BY mx_reason
            ORDER BY episodes DESC, mx_reason
            """

        # #387: the fetches above are mutually independent single-day reads.
        # They used to run as ~13 serial awaits (~27-30 s cold on prod, ~12 s
        # of it the duplicated pinched/phase scan); they now run concurrently,
        # grouped per connection so queries over the same base table share a
        # warm buffer cache. asyncpg connections cannot multiplex queries, so
        # the fan-out borrows from the small bounded module pool
        # (_kpi_fanout_pool_get) while the heaviest unit — the combined
        # pinched-corridor + solar-phase statement — runs on this call's own
        # connection. Response content is byte-identical to the serial
        # version; this is a latency-only change (see the PR #387 golden
        # equivalence runs for the warm-cache benchmark).
        pinched_corridor_columns = (
            "sample_min",
            "samples_with_fraction_readback",
            "avg_band_track_fraction",
            "min_band_track_fraction",
            "max_band_track_fraction",
            "temp_pct",
            "vpd_pct",
            "both_pct",
            "day_pct",
            "night_pct",
            "p95_temp_miss_f",
            "p95_vpd_miss_kpa",
        )
        solar_phase_bucket_columns = (
            "phase_bucket",
            "sample_min",
            "phase_min",
            "phase_max",
            "temp_avg_f",
            "vpd_avg_kpa",
            "min_dp_margin_f",
            "avg_solar_w_m2",
            "served_both_pct",
            "pinched_both_pct",
            "vpd_high_miss_pct",
            "vpd_low_miss_pct",
        )

        async def _pinched_phase_task(c: asyncpg.Connection):
            # One statement returns the single pinched aggregate row joined
            # against the 0-4 phase-bucket rows; split it back into the two
            # shapes the response builder has always consumed. Dict key order
            # matches the historical per-query SELECT column order — it is
            # visible in the serialized response.
            rows = await c.fetch(pinched_phase_sql, d, greenhouse_id)
            first = rows[0] if rows else None
            pinched = {col: first[col] for col in pinched_corridor_columns} if first else {}
            phase_rows = [
                {col: row[f"ph_{col}"] for col in solar_phase_bucket_columns}
                for row in rows
                if row["ph_phase_bucket"] is not None
            ]
            return pinched, phase_rows

        async def _summary_resources_task(c: asyncpg.Connection):
            summary_row = await c.fetchrow(summary_sql, d, greenhouse_id)
            dli_row = await c.fetchrow(dli_sql, d, greenhouse_id)
            if dli_row:
                dli_evidence = DliEvidence.model_validate(dict(dli_row))
            else:
                validity_row = await c.fetchrow(dli_validity_sql, d, greenhouse_id)
                dli_evidence = DliEvidence.model_validate(dict(validity_row))
            water_resource_row = await c.fetchrow(water_sql, d, greenhouse_id)
            energy_resource_row = await c.fetchrow(energy_sql, d, greenhouse_id)
            return summary_row, dli_evidence, water_resource_row, energy_resource_row

        async def _equipment_task(c: asyncpg.Connection):
            cycle_rows = await c.fetch(cycles_snapshot_sql, d, greenhouse_id)
            cycle_read_path = "mv_equipment_runtime_daily"
            if _cycle_snapshot_is_stale(cycle_rows, d, today_local):
                # Completed in-window day the snapshot cannot serve — re-read
                # the live transition derivation rather than presenting stale
                # partial counts as completed-day truth. Bounded by the
                # statement fence: if the live view exceeds it, the tool fails
                # loud instead of lying.
                cycle_rows = await c.fetch(cycles_live_sql, d, greenhouse_id)
                cycle_read_path = "v_equipment_runtime_daily_stale_snapshot_fallback"
            dryout_rows = await c.fetch(dryout_sql, d, greenhouse_id)
            return cycle_rows, cycle_read_path, dryout_rows

        async def _dif_task(c: asyncpg.Connection):
            return await c.fetchrow(dif_sql, d, greenhouse_id)

        async def _action_log_task(c: asyncpg.Connection):
            action_rows = await _fetch_kpi_action_scorecard(c, actions_sql, d, greenhouse_id)
            moisture_rows = await c.fetch(moisture_sql, d, greenhouse_id)
            vpd_policy_row = await c.fetchrow(vpd_policy_sql, d, greenhouse_id)
            vpd_policy_reason_rows = await c.fetch(vpd_policy_reason_sql, d, greenhouse_id)
            return action_rows, moisture_rows, vpd_policy_row, vpd_policy_reason_rows

        pool = await _kpi_fanout_pool_get()

        async def _on_pool(task):
            async with pool.acquire() as pooled_conn:
                return await task(pooled_conn)

        # return_exceptions=True so every branch runs to completion (each is
        # bounded by the per-connection statement timeout) and no cancelled
        # sibling leaks a mid-flight query; the first failure is then
        # re-raised to keep the serial version's fail-loud behavior.
        results = await asyncio.gather(
            _pinched_phase_task(conn),
            _on_pool(_summary_resources_task),
            _on_pool(_equipment_task),
            _on_pool(_dif_task),
            _on_pool(_action_log_task),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        (
            (pinched, phase_rows),
            (summary_row, dli_evidence, water_resource_row, energy_resource_row),
            (cycle_rows, cycle_read_path, dryout_rows),
            dif_row,
            (action_rows, moisture_rows, vpd_policy_row, vpd_policy_reason_rows),
        ) = results

        summary = dict(summary_row) if summary_row else {}
        water_resource = dict(water_resource_row) if water_resource_row else None
        energy_resource = dict(energy_resource_row) if energy_resource_row else None
        if energy_resource and isinstance(energy_resource.get("coefficient_revisions"), str):
            energy_resource["coefficient_revisions"] = json.loads(energy_resource["coefficient_revisions"])
        dif = dict(dif_row) if dif_row else {}

        moisture_sample_count = sum(row["decisions"] for row in moisture_rows)
        moisture_estimator = {
            "sample_count": moisture_sample_count,
            "coverage": "available" if moisture_sample_count else "pending_live_rows",
            "by_action_reason": [dict(row) for row in moisture_rows],
        }
        vpd_policy = dict(vpd_policy_row) if vpd_policy_row else {}
        if vpd_policy:
            vpd_policy["transition_window_min"] = 30
            vpd_policy["episodes_by_mx_reason"] = [dict(row) for row in vpd_policy_reason_rows]

        cycle_by_equipment = {row["equipment"]: dict(row) for row in cycle_rows}
        actuator_cycles = {
            equipment: cycle_by_equipment.get(equipment, {}).get("starts")
            for equipment in (
                "fan1",
                "fan2",
                "heat1",
                "heat2",
                "fog",
                "vent",
                "mister_south",
                "mister_west",
                "mister_center",
                "drip_wall",
                "drip_center",
                "drip_wall_fert",
                "drip_center_fert",
                "mister_south_fert",
                "mister_west_fert",
                "fert_master_valve",
                "grow_light_main",
                "grow_light_grow",
            )
        }
        light_cycle_values = [
            actuator_cycles[name]
            for name in ("grow_light_main", "grow_light_grow")
            if actuator_cycles[name] is not None
        ]
        actuator_cycles["grow_light"] = sum(light_cycle_values) if light_cycle_values else None

        actuator_runtime = {
            f"{equipment}_min": cycle_by_equipment.get(equipment, {}).get("on_minutes")
            for equipment in actuator_cycles
            if equipment != "grow_light"
        }
        light_runtime_values = [
            actuator_runtime[f"{name}_min"]
            for name in ("grow_light_main", "grow_light_grow")
            if actuator_runtime[f"{name}_min"] is not None
        ]
        actuator_runtime["grow_light_min"] = sum(light_runtime_values) if light_runtime_values else None
        for mister in ("mister_south", "mister_west", "mister_center"):
            minutes = actuator_runtime[f"{mister}_min"]
            actuator_runtime[f"{mister}_h"] = round(minutes / 60.0, 3) if minutes is not None else None

        dryout = [dict(row) for row in dryout_rows]
        cycle_quality = [
            {
                key: row[key]
                for key in (
                    "equipment",
                    "is_complete_day",
                    "start_state_known",
                    "open_at_end",
                    "is_deploy_gate_eligible",
                    "quality",
                    "quality_flags",
                    "cycles_under_1m",
                    "cycles_1m_to_5m",
                    "short_cycles_under_5m",
                    "cycles_5m_to_15m",
                    "cycles_15m_plus",
                    "open_pulses_at_cutoff",
                    "peak_transitions_per_hour",
                    "raw_event_rows",
                    "normalized_transition_count",
                    "same_timestamp_duplicate_rows",
                    "redundant_state_rows",
                    "conflicting_timestamp_count",
                )
            }
            for row in cycle_rows
        ]
        firmware_counter_diagnostics = {
            "semantics": (
                "Legacy firmware daily counters are diagnostic only; release "
                "comparisons use v_equipment_runtime_daily raw transitions."
            ),
            "cycles": {key.removeprefix("cycles_"): summary.get(key) for key in summary if key.startswith("cycles_")},
            "runtime": {
                key.removeprefix("runtime_"): summary.get(key) for key in summary if key.startswith("runtime_")
            },
        }
        cycle_day_status = _cycle_day_status(d, today_local)
        vpd_policy["cycle_source"] = {
            "authority": "v_equipment_runtime_daily",
            "read_path": cycle_read_path,
            "semantics": "raw equipment_state transition derivation",
            "all_rows_deploy_gate_eligible": bool(cycle_rows)
            and all(row["is_deploy_gate_eligible"] for row in cycle_rows),
            # #389: only completed local days participate in deploy-gate cycle
            # comparison; the current (partial) day's transition counts stay
            # readable — honest undercounts, never firmware-counter inflation —
            # but are excluded from gates, and future-dated targets carry no
            # evidence at all.
            "deploy_gate": {
                "eligible_days": "completed local days with is_deploy_gate_eligible rows only",
                "target_day_status": cycle_day_status,
                "excluded_from_deploy_gate": cycle_day_status != "complete"
                or not cycle_rows
                or not all(row["is_deploy_gate_eligible"] for row in cycle_rows),
            },
            "equipment": cycle_quality,
        }
        vpd_policy["firmware_counter_diagnostics"] = firmware_counter_diagnostics
        vpd_policy["realized_solar_night_dryout"] = project_realized_solar_night_dryout(dryout)
        pending_metrics = []
        if not moisture_sample_count:
            pending_metrics.append("moisture_estimator: source path wired; waiting for OTA/deploy/live rows")
        if cycle_day_status == "future_date_excluded":
            pending_metrics.append(
                "actuator_cycles_runtime: target date is future-dated; excluded from deploy-gate cycle comparison"
            )
        elif cycle_day_status == "partial_day_excluded":
            pending_metrics.append(
                "actuator_cycles_runtime: current local day is partial; counts are honest "
                "mid-day undercounts and excluded from deploy-gate cycle comparison"
            )
        elif not cycle_rows:
            pending_metrics.append("actuator_cycles_runtime: no transition-derived rows for date")
        elif any(not row["is_deploy_gate_eligible"] for row in cycle_rows):
            pending_metrics.append(
                "actuator_cycles_runtime: transition evidence is partial or quarantined; not deploy-gate eligible"
            )
        if not dryout:
            pending_metrics.append("solar_night_dryout: no eligible opportunity or admitted-action episodes for date")
        elif any(row["dryout_disposition"] != "effective" for row in dryout):
            pending_metrics.append(
                "solar_night_dryout: ineffective, blocked, or insufficient-evidence "
                "episodes remain unresolved; this is not a completed control fix"
            )
        try:
            actions = [OutcomeKpiActionRow.model_validate(dict(row)) for row in action_rows]
            response = OutcomeKpiResponse(
                date=summary.get("date", d),
                greenhouse_id=summary.get("greenhouse_id", greenhouse_id),
                semantics=(
                    "ADR-0004 outcome view: float inside the crop corridor and act at edges. "
                    "Resource terms are eligible only when resource_evidence marks them "
                    "available_for_scoring; modeled and measured scopes never collapse."
                ),
                coverage=OutcomeKpiCoverage(
                    dli="unavailable",
                    actuator_cycles_runtime=(
                        "available"
                        if cycle_day_status == "complete"
                        and cycle_rows
                        and all(row["is_deploy_gate_eligible"] for row in cycle_rows)
                        else "unavailable"
                        if cycle_day_status == "future_date_excluded"
                        else "pending"
                    ),
                    moisture_estimator="available" if moisture_sample_count else "pending",
                    water_use=(
                        "available"
                        if water_resource and water_resource.get("available_for_scoring")
                        else "degraded"
                        if water_resource
                        else "unavailable"
                    ),
                    resource_accounting=(
                        "available"
                        if water_resource
                        and water_resource.get("available_for_scoring")
                        and energy_resource
                        and energy_resource.get("modeled_available_for_scoring")
                        else "degraded"
                        if water_resource or energy_resource
                        else "unavailable"
                    ),
                ),
                served_corridor={
                    "attributable_pct": summary.get("compliance_v2_attributable_pct"),
                    "raw_pct": summary.get("compliance_v2_raw_pct"),
                    "unachievable_frac": summary.get("compliance_v2_unachievable_frac"),
                    "binary_both_pct": summary.get("compliance_pct"),
                    "binary_temp_pct": summary.get("temp_compliance_pct"),
                    "binary_vpd_pct": summary.get("vpd_compliance_pct"),
                    "graded_temp_pct": summary.get("graded_temp_compliance_pct"),
                    "graded_vpd_pct": summary.get("graded_vpd_compliance_pct"),
                    "feasibility_unknown_min": summary.get("feasibility_unknown_min"),
                },
                pinched_corridor={
                    "sample_min": pinched.get("sample_min"),
                    "samples_with_fraction_readback": pinched.get("samples_with_fraction_readback"),
                    "avg_band_track_fraction": pinched.get("avg_band_track_fraction"),
                    "min_band_track_fraction": pinched.get("min_band_track_fraction"),
                    "max_band_track_fraction": pinched.get("max_band_track_fraction"),
                    "both_pct": pinched.get("both_pct"),
                    "temp_pct": pinched.get("temp_pct"),
                    "vpd_pct": pinched.get("vpd_pct"),
                    "p95_temp_miss_f": pinched.get("p95_temp_miss_f"),
                    "p95_vpd_miss_kpa": pinched.get("p95_vpd_miss_kpa"),
                    "day_pct": pinched.get("day_pct"),
                    "night_pct": pinched.get("night_pct"),
                },
                vpd_misses_h={
                    "high_stress_h": summary.get("stress_hours_vpd_high"),
                    "low_stress_h": summary.get("stress_hours_vpd_low"),
                    "graded_high_stress_h": summary.get("graded_stress_hours_vpd_high"),
                    "graded_low_stress_h": summary.get("graded_stress_hours_vpd_low"),
                },
                actuator_cycles=actuator_cycles,
                actuator_runtime=actuator_runtime,
                water_use_gal={
                    "quality_filtered_total": (
                        water_resource.get("quality_filtered_meter_gal")
                        if water_resource and water_resource.get("available_for_scoring")
                        else None
                    ),
                    "meter_attributed": (
                        water_resource.get("attributed_gal")
                        if water_resource and water_resource.get("available_for_scoring")
                        else None
                    ),
                    "ambiguous": (
                        water_resource.get("ambiguous_gal")
                        if water_resource and water_resource.get("available_for_scoring")
                        else None
                    ),
                    "manual_or_unattributed": (
                        water_resource.get("manual_or_unattributed_gal")
                        if water_resource and water_resource.get("available_for_scoring")
                        else None
                    ),
                },
                dli=dli_evidence,
                dif={
                    "day_night_temp_delta_f": dif.get("day_night_temp_delta_f"),
                    "day_temp_avg_f": dif.get("day_temp_avg_f"),
                    "night_temp_avg_f": dif.get("night_temp_avg_f"),
                    "day_sample_min": dif.get("day_sample_min"),
                    "night_sample_min": dif.get("night_sample_min"),
                },
                dew_margin={
                    "min_f": summary.get("min_dp_margin_f"),
                    "risk_h": summary.get("dp_risk_hours"),
                },
                energy_cost={
                    "runtime_modeled_kwh": (
                        energy_resource.get("kwh_estimated")
                        if energy_resource and energy_resource.get("modeled_available_for_scoring")
                        else None
                    ),
                    "partial_measured_kwh": (
                        energy_resource.get("measured_kwh")
                        if energy_resource and energy_resource.get("measured_available_for_scoring")
                        else None
                    ),
                    "electric_usd": (
                        summary.get("cost_electric")
                        if energy_resource and energy_resource.get("modeled_available_for_scoring")
                        else None
                    ),
                    "water_usd": (
                        summary.get("cost_water")
                        if water_resource and water_resource.get("available_for_scoring")
                        else None
                    ),
                    "total_usd": (
                        summary.get("cost_total")
                        if water_resource
                        and water_resource.get("available_for_scoring")
                        and energy_resource
                        and energy_resource.get("modeled_available_for_scoring")
                        else None
                    ),
                },
                resource_evidence={
                    "water": water_resource,
                    "energy": energy_resource,
                    "contract": (
                        "accepted meter gallons are conserved across attributed, ambiguous, "
                        "and manual_or_unattributed; command-only runs have null gallons; "
                        "runtime-modeled energy and partial Shelly energy are separate scopes"
                    ),
                },
                action_scorecard=actions,
                solar_phase_buckets=[dict(row) for row in phase_rows],
                moisture_estimator=moisture_estimator,
                vpd_policy=vpd_policy,
                pending_metrics=pending_metrics,
                source_tables=[
                    "daily_summary",
                    "mv_equipment_runtime_daily",
                    "v_equipment_runtime_daily",
                    "fn_realized_solar_night_dryout",
                    "fn_climate_action_daily_scorecard",
                    "v_water_attribution_daily",
                    "v_energy_estimate_reconciliation",
                    "v_resource_accounting_health",
                    "v_dli_daily",
                    "dli_validity_intervals",
                    "climate",
                    "climate_action_log",
                    "setpoint_snapshot",
                    "setpoint_changes",
                ],
            )
        except ValidationError as e:
            return _json(
                {
                    "error": "OutcomeKpiResponse validation failed",
                    "details": json.loads(e.json()),
                    "raw": {
                        "summary": summary,
                        "pinched_corridor": pinched,
                        "dif": dif,
                        "solar_phase_buckets": [dict(row) for row in phase_rows],
                        "action_scorecard": [dict(row) for row in action_rows],
                    },
                }
            )
        return response.model_dump_json()
    finally:
        await conn.close()


@mcp.tool()
async def equipment_state() -> str:
    """Get current state of all greenhouse equipment (relays, misters, heaters, fans, vent)."""
    conn = await _db()
    try:
        rows = await conn.fetch("""
            SELECT equipment, state, to_char(ts AT TIME ZONE 'America/Denver', 'HH24:MI:SS') as since
            FROM (SELECT DISTINCT ON (equipment) equipment, state, ts
                  FROM equipment_state ORDER BY equipment, ts DESC) sub
            WHERE equipment IN ('fan1','fan2','vent','fog','heat1','heat2',
                'mister_south','mister_west','mister_center')
            ORDER BY equipment
        """)
        validated = [EquipmentStateRow.model_validate(dict(r)).model_dump(mode="json") for r in rows]
        return json.dumps(validated)
    finally:
        await conn.close()


@mcp.tool()
async def forecast(hours: int = 72) -> str:
    """Get weather forecast summary for the next N hours (default 72).
    Returns hourly temp, RH, VPD, cloud cover, solar radiation."""
    try:
        hours = max(1, min(int(hours), 168))
    except (TypeError, ValueError):
        return json.dumps({"error": "hours must be an integer between 1 and 168"})
    conn = await _db()
    try:
        try:
            rows = await conn.fetch(
                """
            SELECT to_char(ts AT TIME ZONE 'America/Denver', 'Dy HH24:MI') as time,
                   round(temp_f::numeric,0) as temp, round(rh_pct::numeric,0) as rh,
                   round(vpd_kpa::numeric,2) as vpd, round(cloud_cover_pct::numeric,0) as cloud,
                   round(GREATEST(COALESCE(direct_radiation_w_m2,0),0)::numeric,0) as solar
            FROM (
                SELECT DISTINCT ON (ts) ts, temp_f, rh_pct, vpd_kpa, cloud_cover_pct,
                       direct_radiation_w_m2
                FROM weather_forecast
                WHERE ts > now() AND ts < now() + ($1::int * interval '1 hour')
                ORDER BY ts, fetched_at DESC
            ) sub
            ORDER BY ts
            LIMIT $1
                """,
                hours,
            )
        except asyncpg.QueryCanceledError:
            return _json(
                {
                    "availability": "unavailable",
                    "reason": "db_statement_timeout",
                    "query_budget_ms": MCP_DB_STATEMENT_TIMEOUT_MS,
                    "guidance": "Do not infer benign weather from an unavailable forecast.",
                }
            )
        validated = [ForecastSummaryRow.model_validate(dict(r)).model_dump(mode="json") for r in rows]
        return json.dumps(validated)
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# SETPOINT TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def get_setpoints() -> str:
    """Get all current active setpoints (firmware band values + planner tunables)."""
    conn = await _db()
    try:
        rows = await conn.fetch(
            """
            SELECT parameter, round(value::numeric,3) as value, source,
                   to_char(ts AT TIME ZONE 'America/Denver', 'HH24:MI') as updated
            FROM (SELECT DISTINCT ON (parameter) parameter, value, source, ts
                  FROM setpoint_changes ORDER BY parameter, ts DESC) sub
            WHERE parameter = ANY($1::text[])
            ORDER BY parameter
            """,
            sorted(ALL_TUNABLES),
        )
        validated = [SetpointSummary.model_validate(dict(r)).model_dump(mode="json") for r in rows]
        return json.dumps(validated)
    finally:
        await conn.close()


@mcp.tool()
async def set_tunable(
    parameter: str = "",
    value: float | None = None,
    reason: str = "iris-manual",
    trigger_id: str | None = None,
    planner_instance: str | None = None,
) -> str:
    """Push a single registry-valid tunable to the ESP32 immediately.
    The dispatcher will apply it within 5 minutes.
    Example: set_tunable('fog_escalation_kpa', 0.15, 'fog is 7x more effective than misters')

    trigger_id, planner_instance: required contract v1.5 audit fields for MCP writes.
    Pass through from the trigger banner shown at the bottom of every
    planning event prompt (`trigger_id=<uuid>`, `planner_instance='opus'|'local'`).
    Stamped onto the one-shot setpoint_plan reason and plan_delivery_log so
    SLA monitors can correlate by uuid."""
    normalized_trigger_id: str | None = None
    if not trigger_id:
        return json.dumps(
            {
                "error": "trigger_id is required for set_tunable MCP writes",
                "hint": "Copy trigger_id exactly from the planning prompt audit headers into set_tunable.",
            }
        )
    try:
        normalized_trigger_id = str(UUID(trigger_id))
    except (TypeError, ValueError):
        return json.dumps({"error": "trigger_id must be a valid UUID"})
    if not parameter:
        return json.dumps({"error": "parameter is required"})
    if value is None:
        return json.dumps({"error": "value is required"})

    # Schema-level gate first rejects typos; the registry then blocks
    # operator-only safety rails and readback-only diagnostics.
    if parameter not in ALL_TUNABLES:
        return json.dumps({"error": f"'{parameter}' is not a known tunable — not in verdify_schemas.ALL_TUNABLES"})
    if parameter not in PLANNER_PUSHABLE_REG:
        return json.dumps(
            {
                "error": f"'{parameter}' is not planner-pushable in the tunable registry",
                "allowed": sorted(PLANNER_PUSHABLE_REG),
            }
        )
    if bounds_error := registry_value_error(parameter, value):
        return json.dumps(
            {
                "error": "Tunable value outside registry bounds",
                "parameter": parameter,
                "value": value,
                "details": bounds_error,
            }
        )
    try:
        normalized_value = normalize_planner_value(parameter, value)
    except ValueError as exc:
        return json.dumps(
            {
                "error": "Tunable value cannot be normalized against planner/firmware bounds",
                "parameter": parameter,
                "value": value,
                "details": str(exc),
            }
        )
    if normalized_value != float(value):
        return json.dumps(
            {
                "error": "Tunable value outside strict planner/firmware bounds",
                "parameter": parameter,
                "value": value,
                "nearest_safe": normalized_value,
            }
        )
    value = normalized_value
    if parameter in FORCED_ON_SWITCH_PARAMS and value < 0.5:
        return json.dumps(
            {
                "error": "controller_locked_on",
                "parameter": parameter,
                "value": value,
                "hint": "The unified band-first controller is locked ON; rollback requires an explicit firmware/config rollback outside the planner surface.",
            }
        )

    # Phase-1b: set_tunable writes to setpoint_plan (one-shot waypoint at
    # ts=now()) so the dispatcher's plan-reading cycle doesn't overwrite
    # the iris push within 5 minutes. Observed live 2026-04-21:
    # min_heat_off_s=180 pushed at 11:36, overwritten to 300 from the
    # prior sunrise plan within 4 minutes. setpoint_plan is the dispatcher's
    # actual source of truth; writing there makes iris pushes durable until
    # the next plan supersedes.
    #
    # plan_id format `iris-oneshot-<YYYYMMDD-HHMM>` lets the next set_plan
    # call (which deactivates older plans) distinguish iris tactical pushes
    # from automatic SUNRISE/SUNSET plans and preserve them across boundaries.
    # Contract v1.5 — stamp audit metadata into dedicated setpoint_plan
    # columns for dispatcher propagation; keep the suffix in reason for
    # operator-readable compatibility with older queries.
    audit_suffix_parts = []
    if normalized_trigger_id:
        audit_suffix_parts.append(f"trigger={normalized_trigger_id}")
    if planner_instance:
        audit_suffix_parts.append(f"instance={planner_instance}")
    if audit_suffix_parts:
        reason_with_audit = f"{reason} [{' '.join(audit_suffix_parts)}]"
    else:
        reason_with_audit = reason

    conn = await _db()
    try:
        # Lane C (#584): while an experiment assignment is armed (or legacy
        # writes are disabled) this write becomes a policy proposal instead of
        # an actuation-eligible setpoint_plan row.
        demotion = await demoted_policy_write_gate(conn)
        if demotion is not None:
            return await _record_demoted_policy_proposal(
                conn,
                demotion,
                action="set_tunable",
                trigger_ref=normalized_trigger_id,
                params={parameter: float(value)},
                digest_material={"parameter": parameter, "value": float(value), "reason": reason},
            )
        now_mdt = datetime.now(ZoneInfo("America/Denver"))
        plan_id = f"iris-oneshot-{now_mdt.strftime('%Y%m%d-%H%M')}"
        async with conn.transaction():
            delivery, ledger, attempt_error = await _lock_current_planner_attempt(
                conn,
                normalized_trigger_id,
                planner_instance,
            )
            if attempt_error:
                return json.dumps(attempt_error)
            assert delivery is not None
            expected_action = ledger["expected_action"] if ledger is not None else "any"
            if expected_action == "set_plan":
                terminal = classify_planner_terminal_action(
                    expected_action="set_plan",
                    actual_action="set_tunable",
                )
                updated_delivery_id = await conn.fetchval(
                    """
                    UPDATE plan_delivery_log
                       SET status = 'wrong_action',
                           terminal_action = 'wrong_action',
                           terminal_at = now(),
                           failure_class = $3,
                           result_payload = jsonb_build_object(
                               'attempted_action', 'set_tunable',
                               'parameter', $2::text
                           )
                     WHERE id = $4
                       AND trigger_id = $1::uuid
                       AND status = 'pending'
                     RETURNING id
                    """,
                    normalized_trigger_id,
                    parameter,
                    terminal.failure_class,
                    delivery["id"],
                )
                if updated_delivery_id != delivery["id"]:
                    raise RuntimeError("plan delivery attempt lost its set_tunable wrong-action fence")
                if ledger is not None:
                    updated_ledger_id = await conn.fetchval(
                        """
                        UPDATE planner_trigger_ledger
                           SET status = 'wrong_action',
                               terminal_action = 'wrong_action',
                               terminal_at = now(),
                               failure_class = $2,
                               resolved_at = now(),
                               updated_at = now()
                         WHERE id = $3
                           AND trigger_id = $1::uuid
                           AND plan_delivery_log_id = $4
                           AND status = 'delivered'
                         RETURNING id
                        """,
                        normalized_trigger_id,
                        terminal.failure_class,
                        ledger["id"],
                        delivery["id"],
                    )
                    if updated_ledger_id != ledger["id"]:
                        raise RuntimeError("planner ledger attempt lost its set_tunable wrong-action fence")
                return json.dumps(
                    {
                        "error": "required set_plan trigger cannot be satisfied by set_tunable",
                        "trigger_id": normalized_trigger_id,
                        "status": "wrong_action",
                        "terminal_action": "wrong_action",
                    }
                )
            wrote_at = await conn.fetchval(
                """
                INSERT INTO setpoint_plan
                  (ts, parameter, value, plan_id, source, reason, trigger_id, planner_instance, expires_at)
                VALUES (now(), $1, $2, $3, 'iris', $4, $5::uuid, $6, now() + interval '6 hours')
                ON CONFLICT (ts, parameter, plan_id) DO UPDATE
                  SET value = EXCLUDED.value,
                      reason = EXCLUDED.reason,
                      trigger_id = EXCLUDED.trigger_id,
                      planner_instance = EXCLUDED.planner_instance,
                      expires_at = EXCLUDED.expires_at
                RETURNING ts
                """,
                parameter,
                value,
                plan_id,
                reason_with_audit,
                normalized_trigger_id,
                planner_instance,
            )
            if normalized_trigger_id:
                updated_delivery_id = await conn.fetchval(
                    """
                    UPDATE plan_delivery_log
                       SET resulting_plan_id = $2,
                           plan_written_at   = $3,
                           status            = 'action_completed',
                           terminal_action   = 'set_tunable',
                           terminal_at       = now(),
                           failure_class     = NULL,
                           result_payload    = jsonb_build_object(
                               'parameter', $4::text,
                               'value', $5::double precision
                           )
                     WHERE id = $6
                       AND trigger_id = $1::uuid
                       AND status = 'pending'
                     RETURNING id
                    """,
                    normalized_trigger_id,
                    plan_id,
                    wrote_at,
                    parameter,
                    value,
                    delivery["id"],
                )
                if updated_delivery_id != delivery["id"]:
                    raise RuntimeError("plan delivery attempt lost its set_tunable completion fence")
                if ledger is not None:
                    updated_ledger_id = await conn.fetchval(
                        """
                        UPDATE planner_trigger_ledger
                           SET status = 'action_completed',
                               terminal_action = 'set_tunable',
                               terminal_at = now(),
                               failure_class = NULL,
                               resulting_plan_id = $2,
                               resolved_at = now(),
                               updated_at = now()
                         WHERE id = $3
                           AND trigger_id = $1::uuid
                           AND plan_delivery_log_id = $4
                           AND status = 'delivered'
                         RETURNING id
                        """,
                        normalized_trigger_id,
                        plan_id,
                        ledger["id"],
                        delivery["id"],
                    )
                    if updated_ledger_id != ledger["id"]:
                        raise RuntimeError("planner ledger attempt lost its set_tunable completion fence")
            await _append_plan_activity(
                conn,
                plan_id=plan_id,
                changes=[{"ts": wrote_at, "param": parameter, "value": value}],
                reason=reason,
                trigger_id=normalized_trigger_id,
                planner_instance=planner_instance,
            )
        return json.dumps(
            {
                "ok": True,
                "parameter": parameter,
                "value": value,
                "reason": reason_with_audit,
                "plan_id": plan_id,
                "trigger_id": normalized_trigger_id,
                "planner_instance": planner_instance,
                "delivery_status": "action_completed" if normalized_trigger_id else None,
                "terminal_action": "set_tunable",
                "note": (
                    "Written to setpoint_plan as a one-shot waypoint at now(). "
                    "Dispatcher pushes to ESP32 within 5 minutes and this value "
                    "persists until the next set_plan or set_tunable supersedes."
                ),
            }
        )
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# PLANNER TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def plan_run(mode: str = "normal") -> str:
    """Trigger an ad-hoc MANUAL planning cycle through the same audited path as scheduled triggers."""
    sys.path.insert(0, str(REPO_ROOT / "ingestor"))
    try:
        from iris_planner import CONTEXT_GATHER_FAILED_SENTINEL, gather_context, prepare_delivery_result, send_to_iris

        mode_clean = (mode or "normal").strip().lower()
        context = gather_context()
        label = f"Ad-hoc planning cycle via MCP plan_run(mode={mode_clean})"
        if context == CONTEXT_GATHER_FAILED_SENTINEL:
            result = {
                "delivered": False,
                "event_type": "MANUAL",
                "event_label": label,
                "session_key": None,
                "wake_mode": None,
                "gateway_status": None,
                "gateway_body": "context_gather_failed",
                "status": "delivery_failed",
                "trigger_id": str(uuid4()),
                "instance": "local",
            }
        else:
            if mode_clean in {"ack", "ack_only", "ack-only", "smoke", "validation"}:
                label = f"validation ack-only: {label}"
                context = (
                    "VALIDATION MODE: acknowledge-only smoke. Do not call set_plan or set_tunable. "
                    "Call acknowledge_trigger with the audit trigger_id and planner_instance, "
                    "then stop.\n\n"
                ) + context

            pre_result = prepare_delivery_result("MANUAL", label, instance="local")
            conn = await _db()
            try:
                await _insert_plan_delivery_log(conn, pre_result)
            finally:
                await conn.close()
            result = send_to_iris(
                "MANUAL",
                label,
                context=context,
                instance="local",
                trigger_id=pre_result["trigger_id"],
            )

        conn = await _db()
        try:
            explicit_status = await _insert_plan_delivery_log(conn, result)
        finally:
            await conn.close()

        status = explicit_status or "pending"
        resp = PlanRunResponse(
            ok=bool(result.get("delivered")),
            note="MANUAL event sent to Hermes. Check plan_delivery_log for ack/plan correlation.",
            error=None if result.get("delivered") else result.get("gateway_body"),
            trigger_id=result.get("trigger_id"),
            event_type=result.get("event_type"),
            planner_instance=result.get("instance"),
            session_key=result.get("session_key"),
            status=status,
            hermes_run_id=result.get("hermes_run_id"),
        )
        return resp.model_dump_json(exclude_none=True)
    except Exception as e:
        return PlanRunResponse(ok=False, error=str(e)).model_dump_json(exclude_none=True)


@mcp.tool()
async def plan_status() -> str:
    """Get the current active plan — waypoints, plan_id, hypothesis, compliance."""
    conn = await _db()
    try:
        journal = await conn.fetchrow("""
            SELECT plan_id, to_char(created_at AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') as created,
                   hypothesis, experiment, expected_outcome
            FROM plan_journal
            WHERE plan_id NOT LIKE 'iris-reactive%'
              AND lifecycle_status = 'effective'
              AND valid_from <= now()
              AND expires_at > now()
            ORDER BY created_at DESC LIMIT 1
        """)
        waypoints = await conn.fetch("""
            SELECT to_char(ts AT TIME ZONE 'America/Denver', 'Dy HH24:MI') as time, count(*) as params
            FROM setpoint_plan
            WHERE is_active = true
              AND ts > now()
              AND expires_at > now()
            GROUP BY ts ORDER BY ts LIMIT 15
        """)
        resp = PlanStatusResponse(
            plan=PlanStatusJournal.model_validate(dict(journal)) if journal else None,
            future_waypoints=[PlanStatusWaypoint.model_validate(dict(w)) for w in waypoints],
        )
        return resp.model_dump_json(exclude_none=True)
    finally:
        await conn.close()


@mcp.tool()
async def lessons() -> str:
    """Get active planner lessons (accumulated operational knowledge)."""
    conn = await _db()
    try:
        rows = await conn.fetch("""
            SELECT category, condition, lesson, confidence, times_validated
            FROM planner_lessons WHERE is_active = true AND superseded_by IS NULL
            ORDER BY CASE confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                     times_validated DESC
            LIMIT 10
        """)
        validated = [LessonSummary.model_validate(dict(r)).model_dump(mode="json") for r in rows]
        return json.dumps(validated)
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# DATA QUERY TOOL
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def query(sql: str) -> str:
    """Run a read-only SQL query against the Verdify database.
    Returns up to 100 rows as JSON. Only SELECT queries allowed."""
    sql_stripped = sql.strip()
    sql_upper = sql_stripped.upper()
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
        return json.dumps({"error": "Only SELECT/WITH queries are allowed"})
    # Keep this as a simple one-statement diagnostic path. The DB transaction is
    # read-only below, but rejecting multi-statement text avoids surprising
    # behavior and keeps tool output bounded.
    if ";" in sql_stripped.rstrip(";"):
        return json.dumps({"error": "Only a single read-only statement is allowed"})

    conn = await _db()
    try:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = '5s'")
            rows = await conn.fetch(sql_stripped.rstrip(";"))
        return _json([dict(r) for r in rows[:100]])
    except asyncpg.ReadOnlySQLTransactionError:
        return json.dumps({"error": "Query attempted a write and was rejected by the read-only transaction"})
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# PLANNING TOOLS
# ═══════════════════════════════════════════════════════════════

_REQUIRED_FULL_PLAN_EVENTS = frozenset({"SUNRISE", "SUNSET", "MIDNIGHT", "FORECAST_DEVIATION", "DEVIATION", "FORECAST"})
_LEDGER_BACKED_EVENTS = frozenset(
    {
        "SUNRISE",
        "SUNSET",
        "MIDNIGHT",
        "WEEKLY",
        "SOLAR_MAX",
        "TRANSITION",
        "FORECAST_DEVIATION",
        "FORECAST",
        "DEVIATION",
        "HEARTBEAT",
    }
)


async def _lock_current_planner_attempt(
    conn: asyncpg.Connection,
    trigger_id: str,
    planner_instance: str | None,
) -> tuple[asyncpg.Record | None, asyncpg.Record | None, dict[str, object] | None]:
    """Lock and validate the delivery/ledger attempt that may replace a plan."""
    delivery = await conn.fetchrow(
        """
        SELECT id, trigger_id, event_type, event_label, status, instance
          FROM plan_delivery_log
         WHERE trigger_id = $1::uuid
         FOR UPDATE
        """,
        trigger_id,
    )
    if delivery is None:
        return (
            None,
            None,
            {
                "error": "trigger_id not found in plan_delivery_log",
                "trigger_id": trigger_id,
            },
        )
    if delivery["status"] != "pending":
        return (
            delivery,
            None,
            {
                "error": "trigger_id is not the current writable attempt",
                "trigger_id": trigger_id,
                "status": delivery["status"],
            },
        )
    if planner_instance and delivery["instance"] and planner_instance != delivery["instance"]:
        return (
            delivery,
            None,
            {
                "error": "planner_instance does not match plan_delivery_log",
                "trigger_id": trigger_id,
                "planner_instance": planner_instance,
                "delivery_instance": delivery["instance"],
            },
        )

    ledger = await conn.fetchrow(
        """
        SELECT id, trigger_id, plan_delivery_log_id, event_type, expected_action, status
          FROM planner_trigger_ledger
         WHERE trigger_id = $1::uuid
           AND plan_delivery_log_id = $2
         FOR UPDATE
        """,
        trigger_id,
        delivery["id"],
    )
    validation_ack = (delivery["event_label"] or "").lower().startswith("validation") and "ack-only" in (
        delivery["event_label"] or ""
    ).lower()
    ledger_backed_event = delivery["event_type"] in _LEDGER_BACKED_EVENTS
    required_event = delivery["event_type"] in _REQUIRED_FULL_PLAN_EVENTS and not validation_ack
    if ledger_backed_event and ledger is None:
        return (
            delivery,
            None,
            {
                "error": "scheduled trigger attempt is stale or superseded",
                "trigger_id": trigger_id,
            },
        )
    if ledger is not None and ledger["status"] != "delivered":
        return (
            delivery,
            ledger,
            {
                "error": "planner trigger ledger attempt is not currently delivered",
                "trigger_id": trigger_id,
                "ledger_status": ledger["status"],
            },
        )
    if required_event and ledger is not None and ledger["expected_action"] != "set_plan":
        return (
            delivery,
            ledger,
            {
                "error": "required trigger ledger does not expect set_plan",
                "trigger_id": trigger_id,
                "expected_action": ledger["expected_action"],
            },
        )
    return delivery, ledger, None


@mcp.tool()
async def set_plan(
    plan_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=18,
            pattern=r"^iris-\d{8}-\d{4}$",
            description="Unique plan ID in iris-YYYYMMDD-HHMM format.",
        ),
    ] = "",
    hypothesis: Annotated[
        str,
        Field(
            min_length=1,
            max_length=SET_PLAN_HYPOTHESIS_MAX_CHARS,
            description=(
                "Compact hypothesis. SUNRISE/SUNSET require one bare "
                "PlanHypothesisStructured JSON object with at most 2 stress windows and 3 rationales."
            ),
        ),
    ] = "",
    transitions: Annotated[
        str,
        Field(
            min_length=2,
            max_length=SET_PLAN_TRANSITIONS_MAX_CHARS,
            description=(
                "JSON array of 3-8 time-ordered waypoints. Every waypoint must include all "
                "ClimateIntent fields and an optional reason of at most 120 characters."
            ),
        ),
    ] = "",
    experiment: Annotated[
        str,
        Field(
            max_length=SET_PLAN_EXPERIMENT_MAX_CHARS,
            description="Optional one-line experiment description (240 characters maximum).",
        ),
    ] = "",
    expected_outcome: Annotated[
        str,
        Field(
            max_length=SET_PLAN_EXPECTED_OUTCOME_MAX_CHARS,
            description="Optional measurable prediction (320 characters maximum).",
        ),
    ] = "",
    trigger_id: Annotated[
        str | None,
        Field(max_length=36, description="Audit trigger UUID copied exactly from the planning prompt."),
    ] = None,
    planner_instance: Annotated[
        str | None,
        Field(max_length=5, description="Audit planner instance: local or opus."),
    ] = None,
    valid_from: Annotated[
        str | None,
        Field(max_length=40, description="Optional ISO-8601 plan validity start."),
    ] = None,
    expires_at: Annotated[
        str | None,
        Field(max_length=40, description="Optional ISO-8601 plan expiry."),
    ] = None,
) -> str:
    """Write a 72-hour setpoint plan with multiple time-based waypoints.
    Deactivates all existing future waypoints, writes new ones, and logs a plan journal entry.
    The dispatcher executes these on schedule — the greenhouse follows the plan even if the planner goes offline.

    plan_id: unique ID like 'iris-YYYYMMDD-HHMM'
    hypothesis: compact statement of what this plan should achieve, at most
        2,200 characters. SUNRISE/SUNSET require a bare
        PlanHypothesisStructured JSON object (conditions + no more than two
        stress_windows + no more than three rationales). Do not repeat the
        assembled context.
    transitions: JSON array of 3-8 objects: [{"ts": "ISO8601-with-TZ",
        "climate_intent": {...}, "reason": "..."}]. Every ClimateIntent field
        is required; keep each reason at or below 120 characters.
    experiment: optional one-line description, at most 240 characters
    expected_outcome: optional measurable prediction, at most 320 characters
    valid_from, expires_at: optional ISO-8601 validity bounds. When omitted,
        validity starts at the first transition and expires six hours after the
        final transition; the total envelope cannot exceed 78 hours.
    trigger_id, planner_instance: required contract v1.5 audit fields. Pass
        through from the audit-headers banner shown at the bottom of every
        planning event prompt (`trigger_id=<uuid>`, `planner_instance='opus'|'local'`).
        Stamped onto plan_journal so SLA monitors and audit queries can
        correlate plans to deliveries by uuid (not 2h time-window fallback).

    Keep all decoded string arguments below 9,000 characters total. The MCP
    boundary rejects totals above 10,000 before opening a database connection."""
    # Sprint 20: validate the whole envelope through Plan schema before any DB writes.
    # This rejects unknown tunables, inverted temp/VPD bands, non-monotonic transitions,
    # bad plan_id format, timezone-naive timestamps, etc. — at the MCP boundary, so
    # partial plans never land in setpoint_plan.
    decoded_argument_chars = _set_plan_decoded_argument_chars(
        plan_id=plan_id,
        hypothesis=hypothesis,
        transitions=transitions,
        experiment=experiment,
        expected_outcome=expected_outcome,
        trigger_id=trigger_id,
        planner_instance=planner_instance,
        valid_from=valid_from,
        expires_at=expires_at,
    )
    if decoded_argument_chars > SET_PLAN_ARGUMENTS_MAX_CHARS:
        return json.dumps(
            {
                "error": "set_plan decoded arguments exceed the compact tool-call contract",
                "decoded_argument_chars": decoded_argument_chars,
                "max_decoded_argument_chars": SET_PLAN_ARGUMENTS_MAX_CHARS,
                "hint": (
                    "Keep the whole decoded argument object under 9,000 characters: use 3-8 "
                    "compact transitions, reasons <=120 characters, and do not repeat context."
                ),
            }
        )

    normalized_trigger_id: str | None = None
    if not trigger_id:
        return json.dumps(
            {
                "error": "trigger_id is required for set_plan MCP writes",
                "hint": "Copy trigger_id exactly from the planning prompt audit headers into set_plan.",
            }
        )
    try:
        normalized_trigger_id = str(UUID(trigger_id))
    except (TypeError, ValueError):
        return json.dumps({"error": "trigger_id must be a valid UUID"})
    if not plan_id:
        return json.dumps({"error": "plan_id is required"})
    if not transitions:
        return json.dumps({"error": "transitions is required"})

    try:
        waypoints_raw = json.loads(transitions)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON in transitions: {e}"})

    climate_intent_errors = _climate_intent_waypoint_errors(waypoints_raw)
    if climate_intent_errors:
        return json.dumps(
            {
                "error": "set_plan requires climate_intent on every transition",
                "details": climate_intent_errors[:10],
            }
        )
    conn_for_intent = await _db()
    try:
        active_tier1_params = await _fetch_active_tier1_params(conn_for_intent)
    finally:
        await conn_for_intent.close()
    try:
        waypoints_raw, climate_intent_records = _materialize_climate_intent_waypoints(
            waypoints_raw,
            active_tier1_params,
        )
    except ValidationError as e:
        return json.dumps(
            {
                "error": "ClimateIntent validation failed",
                "details": json.loads(e.json(include_input=False))[:10],
            }
        )
    except ValueError as e:
        return json.dumps({"error": "ClimateIntent materialization failed", "detail": str(e)})

    try:
        plan = Plan.model_validate(
            {
                "plan_id": plan_id,
                "hypothesis": hypothesis,
                "experiment": experiment or None,
                "expected_outcome": expected_outcome or None,
                "transitions": waypoints_raw,
                "valid_from": valid_from,
                "expires_at": expires_at,
            }
        )
    except ValidationError as e:
        return json.dumps({"error": "Plan validation failed", "details": json.loads(e.json(include_input=False))[:10]})

    writable_params = [param for wp in plan.transitions for param in wp.params if param not in BAND_OWNED_PARAMS]
    if not writable_params:
        return json.dumps(
            {
                "error": "Plan contains only dispatcher-owned policy params; these are read-only context",
                "band_owned_params": sorted(BAND_OWNED_PARAMS),
            }
        )
    missing_required = []
    for idx, wp in enumerate(plan.transitions):
        missing = sorted(PLAN_REQUIRED_PARAMS - set(wp.params))
        if missing:
            missing_required.append({"transition_index": idx, "ts": wp.ts.isoformat(), "missing": missing})
    if missing_required:
        return json.dumps(
            {
                "error": f"Plan transitions must include all {len(PLAN_REQUIRED_PARAMS)} tactical Tier 1 params",
                "missing_required_params": missing_required,
                "required_params": sorted(PLAN_REQUIRED_PARAMS),
                "band_owned_params": sorted(BAND_OWNED_PARAMS),
            }
        )
    non_policy_params = sorted(
        {
            param
            for wp in plan.transitions
            for param in wp.params
            if param not in BAND_OWNED_PARAMS and param not in PLANNER_PUSHABLE_REG
        }
    )
    if non_policy_params:
        return json.dumps(
            {
                "error": "Plan contains non-policy tunables; MCP only persists planner-policy params",
                "non_policy_params": non_policy_params,
                "allowed_params": sorted(PLANNER_PUSHABLE_REG),
                "band_owned_params": sorted(BAND_OWNED_PARAMS),
            }
        )

    # Phase 2b (Iris loop overhaul): extract structured hypothesis and enforce
    # presence for SUNRISE/SUNSET. Two parser paths:
    #   1. Fenced ```json …``` block anywhere in the hypothesis (original)
    #   2. Bare top-level JSON when the hypothesis field is entirely JSON
    #      (common GPT-5.5 output mode — flagged by Codex audit 2026-05-10)
    structured_payload: str | None = None
    structured_warning: str | None = None
    import re as _re

    def _try_parse_structured(blob: str) -> tuple[str | None, str | None]:
        try:
            ps = PlanHypothesisStructured.model_validate_json(blob)
            return ps.model_dump_json(), None
        except ValidationError as ee:
            return None, f"structured hypothesis present but invalid: {ee.errors()[:3]}"

    # Path 1: fenced ```json block
    m = _re.search(r"```json\s*(\{.*?\})\s*```", hypothesis, _re.DOTALL)
    if m:
        structured_payload, structured_warning = _try_parse_structured(m.group(1))

    # Path 2: bare top-level JSON (GPT-5.5 often omits the fence)
    if structured_payload is None:
        stripped = (hypothesis or "").strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            sp, sw = _try_parse_structured(stripped)
            if sp is not None:
                structured_payload = sp
            elif structured_warning is None:
                structured_warning = sw

    # P1a (B6): the planner's crop-band targets (temp_low/high, vpd_low/high,
    # per-zone vpd targets) are dropped from setpoint_plan below (clamp-safe — the
    # dispatcher/curve owns the served band), but they were ALSO silently excluded
    # from the plan_journal audit, so plan-accuracy had no record of what band the
    # planner intended. Restore band-param *recording* (NOT actuation): include the
    # crop-band param NAMES the planner touched in params_changed so v_plan_accuracy
    # / v_plan_compliance can grade planned-vs-served band against the new curve.
    # Lighting band-owned params (gl_*) stay excluded — they are not the crop band.
    planned_band: list[dict] = []
    for wp in plan.transitions:
        band_targets = {p: float(v) for p, v in wp.params.items() if p in CROP_BAND_PARAMS}
        if band_targets:
            planned_band.append(
                {"ts": wp.ts.isoformat() if hasattr(wp.ts, "isoformat") else str(wp.ts), **band_targets}
            )

    params_seen = sorted(
        {
            param
            for wp in plan.transitions
            for param in wp.params
            if param not in BAND_OWNED_PARAMS or param in CROP_BAND_PARAMS
        }
    )

    conditions_summary: str | None = None
    if structured_payload:
        try:
            structured = json.loads(structured_payload)
            conditions = structured.get("conditions") or {}
            stress_windows = structured.get("stress_windows") or []
            parts: list[str] = []
            if conditions.get("notes"):
                parts.append(str(conditions["notes"]))
            weather_bits = []
            for key, label in (
                ("outdoor_temp_peak_f", "outdoor peak"),
                ("outdoor_rh_min_pct", "RH min"),
                ("solar_peak_w_m2", "solar peak"),
                ("cloud_cover_avg_pct", "cloud cover"),
            ):
                if conditions.get(key) is not None:
                    weather_bits.append(f"{label}: {conditions[key]}")
            if weather_bits:
                parts.append(", ".join(weather_bits))
            if stress_windows:
                labels = []
                for window in stress_windows[:4]:
                    labels.append(
                        "{kind} {start}-{end} {severity}".format(
                            kind=window.get("kind", "stress"),
                            start=window.get("start", "?"),
                            end=window.get("end", "?"),
                            severity=window.get("severity", "?"),
                        )
                    )
                parts.append("stress windows: " + "; ".join(labels))
            conditions_summary = " | ".join(parts)[:2000] if parts else None
        except (TypeError, ValueError):
            conditions_summary = None

    conn = await _db()
    try:
        # Lane C (#584): demoted plans become ONE policy proposal carrying the
        # first transition's policy params (the immediately-actuatable slice)
        # plus a digest of the whole plan envelope for audit.
        demotion = await demoted_policy_write_gate(conn)
        if demotion is not None:
            first_params = {
                param: float(value)
                for param, value in plan.transitions[0].params.items()
                if param not in BAND_OWNED_PARAMS and param in PLANNER_PUSHABLE_REG
            }
            return await _record_demoted_policy_proposal(
                conn,
                demotion,
                action="set_plan",
                trigger_ref=normalized_trigger_id,
                params=first_params,
                digest_material={
                    "plan_id": plan.plan_id,
                    "transitions": [
                        {"ts": wp.ts.isoformat(), "params": {k: float(v) for k, v in wp.params.items()}}
                        for wp in plan.transitions
                    ],
                },
            )
        async with conn.transaction():
            db_now = await conn.fetchval("SELECT now()")
            coverage_error = plan_current_coverage_error(plan, db_now)
            if coverage_error:
                return json.dumps(
                    {
                        "error": "Plan does not provide current required coverage",
                        "detail": coverage_error,
                    }
                )
            delivery, ledger, attempt_error = await _lock_current_planner_attempt(
                conn,
                normalized_trigger_id,
                planner_instance,
            )
            if attempt_error:
                return json.dumps(attempt_error)
            assert delivery is not None

            existing = await conn.fetchval("SELECT 1 FROM plan_journal WHERE plan_id = $1", plan.plan_id)
            if existing:
                return json.dumps({"error": f"plan_id {plan.plan_id!r} already exists; generate a new plan_id"})

            # Phase 2b: SUNRISE/SUNSET MUST carry a valid hypothesis_structured.
            # Look up the trigger's event_type from planner_trigger_ledger and
            # reject if the structured block is missing or invalid.
            event_type = ledger["event_type"] if ledger is not None else delivery["event_type"]
            if event_type in ("SUNRISE", "SUNSET") and structured_payload is None:
                return json.dumps(
                    {
                        "error": f"{event_type} plans require a valid PlanHypothesisStructured block",
                        "detail": structured_warning or "no JSON block found in hypothesis",
                        "required_top_level_keys": ["conditions", "stress_windows", "rationale"],
                        "accepted_formats": [
                            "fenced ```json {...} ``` block in the hypothesis prose",
                            "bare top-level JSON (the entire hypothesis field is one JSON object)",
                        ],
                        "example_template": {
                            "conditions": {
                                "outdoor_temp_peak_f": 75.0,
                                "outdoor_rh_min_pct": 25.0,
                                "solar_peak_w_m2": 900,
                                "cloud_cover_avg_pct": 30,
                                "notes": "describe the dominant weather drivers and any unusual conditions",
                            },
                            "stress_windows": [
                                {
                                    "kind": "vpd_high",
                                    "start": "2026-05-10T11:00:00-06:00",
                                    "end": "2026-05-10T17:00:00-06:00",
                                    "severity": "medium",
                                    "mitigation": "engage 1.3, gap 25s, fog_escalation 0.30",
                                }
                            ],
                            "rationale": [
                                {
                                    "parameter": "mister_engage_kpa",
                                    "old_value": 1.6,
                                    "new_value": 1.3,
                                    "forecast_anchor": "RH < 15% from 11:00-17:00",
                                    "expected_effect": "drop VPD-high stress hours from 4.5 to under 2.0",
                                }
                            ],
                        },
                    }
                )

            # One full plan is effective per greenhouse. Expire or supersede the
            # prior journal row before inserting the replacement so the partial
            # unique index is a database-level race guard, not an application
            # convention. A full plan also supersedes prior Iris one-shots.
            await conn.execute(
                """
                UPDATE plan_journal
                   SET lifecycle_status = CASE WHEN expires_at <= now() THEN 'expired' ELSE 'superseded' END
                 WHERE greenhouse_id = 'vallery'
                   AND lifecycle_status = 'effective'
                """
            )
            await conn.execute(
                """UPDATE setpoint_plan SET is_active = false
                   WHERE is_active = true
                     AND source = 'iris'"""
            )

            # Write new waypoints. Crop-band and lighting-policy params are
            # read-only planner context, owned by DB policy functions +
            # dispatcher; dropping them here prevents future clamp storms from
            # semantically valid but owner-misaligned plans.
            rows_written = 0
            band_params_dropped = 0
            forced_on_params = 0
            changes: list[dict[str, Any]] = []
            for wp in plan.transitions:
                for param, value in wp.params.items():
                    if param in BAND_OWNED_PARAMS:
                        band_params_dropped += 1
                        continue
                    if param in FORCED_ON_SWITCH_PARAMS and float(value) < 0.5:
                        value = 1.0
                        forced_on_params += 1
                    await conn.execute(
                        """INSERT INTO setpoint_plan
                             (ts, parameter, value, plan_id, source, reason, created_at,
                              is_active, greenhouse_id, trigger_id, planner_instance, expires_at)
                           VALUES ($1, $2, $3, $4, 'iris', $5, now(), true, 'vallery', $6::uuid, $7, $8)""",
                        wp.ts,
                        param,
                        float(value),
                        plan.plan_id,
                        wp.reason or "",
                        normalized_trigger_id,
                        planner_instance,
                        plan.expires_at,
                    )
                    rows_written += 1
                    changes.append({"ts": wp.ts, "param": param, "value": float(value)})

            # Write journal entry — structured JSONB column populated only if
            # the PlanHypothesisStructured block was present AND valid.
            # Contract v1.4 §2.C — stamp planner_instance + trigger_id when the
            # caller passed them through from the prompt's audit-headers banner.
            # Both columns nullable; NULL means "pre-v1.4 path or operator
            # injection that didn't carry headers."
            journal_created_at = await conn.fetchval(
                """INSERT INTO plan_journal
                     (plan_id, created_at, hypothesis, experiment, expected_outcome,
                      hypothesis_structured, greenhouse_id, planner_instance, trigger_id,
                      conditions_summary, params_changed, climate_intents,
                      climate_intent_version, valid_from, expires_at, lifecycle_status)
                   VALUES ($1, now(), $2, $3, $4, $5::jsonb, 'vallery', $6, $7::uuid,
                           $8, $9::text[], $10::jsonb, $11, $12, $13, 'effective')
                   RETURNING created_at""",
                plan.plan_id,
                plan.hypothesis,
                plan.experiment,
                plan.expected_outcome,
                structured_payload,
                planner_instance,
                normalized_trigger_id,
                conditions_summary,
                params_seen,
                json.dumps(climate_intent_records) if climate_intent_records else None,
                CLIMATE_INTENT_CONTRACT_VERSION if climate_intent_records else None,
                plan.valid_from,
                plan.expires_at,
            )
            if normalized_trigger_id:
                updated_delivery_id = await conn.fetchval(
                    """
                    UPDATE plan_delivery_log
                       SET resulting_plan_id = $2,
                           plan_written_at   = $3,
                           status            = 'plan_written',
                           terminal_action   = 'set_plan',
                           terminal_at       = now(),
                           failure_class     = NULL,
                           result_payload    = jsonb_build_object(
                               'plan_id', $2::text,
                               'valid_from', $4::timestamptz,
                               'expires_at', $5::timestamptz
                           )
                     WHERE id = $6
                       AND trigger_id = $1::uuid
                       AND status = 'pending'
                     RETURNING id
                    """,
                    normalized_trigger_id,
                    plan.plan_id,
                    journal_created_at,
                    plan.valid_from,
                    plan.expires_at,
                    delivery["id"],
                )
                if updated_delivery_id != delivery["id"]:
                    raise RuntimeError("plan delivery attempt lost its write fence")
                if ledger is not None:
                    updated_ledger_id = await conn.fetchval(
                        """
                    UPDATE planner_trigger_ledger
                       SET status = 'plan_written',
                           terminal_action = 'set_plan',
                           terminal_at = now(),
                           failure_class = NULL,
                           resulting_plan_id = $2,
                           resolved_at = now(),
                           updated_at = now()
                     WHERE id = $3
                       AND trigger_id = $1::uuid
                       AND plan_delivery_log_id = $4
                       AND status = 'delivered'
                     RETURNING id
                    """,
                        normalized_trigger_id,
                        plan.plan_id,
                        ledger["id"],
                        delivery["id"],
                    )
                    if updated_ledger_id != ledger["id"]:
                        raise RuntimeError("planner trigger ledger attempt lost its write fence")
            await _append_plan_activity(
                conn,
                plan_id=plan.plan_id,
                changes=changes,
                reason=None,
                trigger_id=normalized_trigger_id,
                planner_instance=planner_instance,
            )

        # Sprint 20 Phase 6: drop a trigger file so verdify-plan-publish.path
        # fires and regenerates the daily plan page. Local-SSD location so
        # inotify actually works (NFS path units don't fire reliably).
        try:
            from datetime import UTC
            from datetime import datetime as _dt

            if SITE_PUBLISH_TRIGGER_PATH:
                trigger_path = Path(SITE_PUBLISH_TRIGGER_PATH)
                trigger_path.parent.mkdir(parents=True, exist_ok=True)
                trigger_path.write_text(f"{plan.plan_id}\n{_dt.now(UTC).isoformat()}\n")
        except Exception as e:  # never block plan persistence on trigger failures
            log_msg = f"plan-publish trigger write failed (non-fatal): {e}"
            print(log_msg)

        result = {
            "ok": True,
            "plan_id": plan.plan_id,
            "transitions": len(plan.transitions),
            "rows_written": rows_written,
            "band_params_dropped": band_params_dropped,
            "planned_band_recorded": len(planned_band),
            "forced_on_params": forced_on_params,
            "climate_intent_segments": len(climate_intent_records),
            "climate_intent_version": CLIMATE_INTENT_CONTRACT_VERSION if climate_intent_records else None,
            "climate_intent_guardrails": sum(len(record.get("guardrails", ())) for record in climate_intent_records),
            "structured_hypothesis": structured_payload is not None,
            "trigger_id": normalized_trigger_id,
            "planner_instance": planner_instance,
            "delivery_status": "plan_written" if normalized_trigger_id else None,
            "terminal_action": "set_plan",
            "valid_from": plan.valid_from.isoformat() if plan.valid_from else None,
            "expires_at": plan.expires_at.isoformat() if plan.expires_at else None,
            "note": "Dispatcher will execute waypoints on schedule. Old future waypoints deactivated.",
        }
        if structured_warning:
            result["structured_warning"] = structured_warning
        return json.dumps(result)
    finally:
        await conn.close()


@mcp.tool()
async def policy_template_propose(
    assignment_receipt: str = "",
    policy_template_id: str = "",
    prediction: str = "",
    rationale: str = "",
) -> str:
    """Propose a pre-qualified policy template for the current experiment assignment.

    The experiment planner arm's ONLY actuation-eligible output (#584, audit
    §8.8): an opaque template selection. The proposal is recorded append-only;
    the policy arbiter compiles it against the active assignment and (in live
    mode) admits it through the atomic vector path. Nothing actuates directly
    from this call, and the response never reveals the arm or treatment.

    assignment_receipt: the opaque receipt from the planning trigger (it does
        not identify the experiment or the arm).
    policy_template_id: one of the pre-qualified template ids offered in the
        trigger context.
    prediction, rationale: the falsifiable prediction and reasoning to freeze
        alongside the selection (stored in the append-only context snapshot).
    """
    if not assignment_receipt:
        return json.dumps({"error": "assignment_receipt is required"})
    if not policy_template_id:
        return json.dumps({"error": "policy_template_id is required"})
    try:
        receipt = str(UUID(assignment_receipt))
        template_id = str(UUID(policy_template_id))
    except (TypeError, ValueError):
        return json.dumps({"error": "assignment_receipt and policy_template_id must be valid UUIDs"})

    conn = await _db()
    try:
        try:
            proposal_id = await submit_policy_proposal(
                conn,
                producer="ai",
                trigger_ref=f"receipt:{receipt}",
                proposed_template_id=template_id,
                context={"prediction": prediction, "rationale": rationale},
                assignment_id=receipt,
                actor="mcp-policy_template_propose",
            )
        except Exception as exc:
            return json.dumps(
                {
                    "error": "template selection could not be recorded",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
        return json.dumps(
            {
                "ok": True,
                "proposal_id": proposal_id,
                "state": "proposed",
                "note": (
                    "Template selection recorded as a policy proposal. The arbiter compiles it "
                    "for the current assignment; nothing actuates directly from this call."
                ),
            }
        )
    finally:
        await conn.close()


@mcp.tool()
async def acknowledge_trigger(
    trigger_id: str,
    reason: str,
    planner_instance: str | None = None,
    neutral_fallback: bool = False,
) -> str:
    """Record that Iris read a planning trigger and intentionally wrote no plan.

    Use this only when a TRANSITION/HEARTBEAT cycle needs no setpoint
    change. It turns the matching plan_delivery_log row from pending -> acked,
    so SLA monitors can distinguish "read/no action" from "silent drop"."""
    try:
        tid = UUID(trigger_id)
    except (TypeError, ValueError):
        return json.dumps({"error": "trigger_id must be a valid UUID"})

    reason = (reason or "").strip()
    if not reason:
        return json.dumps({"error": "reason is required"})
    if len(reason) > 1000:
        return json.dumps({"error": "reason must be <= 1000 characters"})

    conn = await _db()
    try:
        async with conn.transaction():
            existing, ledger, attempt_error = await _lock_current_planner_attempt(
                conn,
                str(tid),
                planner_instance,
            )
            if attempt_error:
                return _json(attempt_error)
            assert existing is not None
            expected_action = ledger["expected_action"] if ledger is not None else "any"
            event_label = (existing["event_label"] or "").lower()
            is_validation_ack = event_label.startswith("validation") and "ack-only" in event_label
            required_full_plan = expected_action == "set_plan" and not is_validation_ack
            if required_full_plan and not neutral_fallback:
                terminal = classify_planner_terminal_action(
                    expected_action="set_plan",
                    actual_action="acknowledge_trigger",
                )
                updated_delivery_id = await conn.fetchval(
                    """
                    UPDATE plan_delivery_log
                       SET status = 'wrong_action',
                           terminal_action = 'wrong_action',
                           terminal_at = now(),
                           failure_class = $2,
                           result_payload = jsonb_build_object('attempted_action', 'acknowledge_trigger')
                     WHERE id = $3
                       AND trigger_id = $1::uuid
                       AND status = 'pending'
                     RETURNING id
                    """,
                    str(tid),
                    terminal.failure_class,
                    existing["id"],
                )
                if updated_delivery_id != existing["id"]:
                    raise RuntimeError("plan delivery attempt lost its acknowledge wrong-action fence")
                if ledger is not None:
                    updated_ledger_id = await conn.fetchval(
                        """
                        UPDATE planner_trigger_ledger
                           SET status = 'wrong_action',
                               terminal_action = 'wrong_action',
                               terminal_at = now(),
                               failure_class = $2,
                               resolved_at = now(),
                               updated_at = now()
                         WHERE id = $3
                           AND trigger_id = $1::uuid
                           AND plan_delivery_log_id = $4
                           AND status = 'delivered'
                         RETURNING id
                        """,
                        str(tid),
                        terminal.failure_class,
                        ledger["id"],
                        existing["id"],
                    )
                    if updated_ledger_id != ledger["id"]:
                        raise RuntimeError("planner ledger attempt lost its acknowledge wrong-action fence")
                return _json(
                    {
                        "error": "required set_plan trigger received the wrong terminal action",
                        "trigger_id": str(tid),
                        "event_type": existing["event_type"],
                        "event_label": existing["event_label"],
                        "status": "wrong_action",
                        "terminal_action": "wrong_action",
                    }
                )

            terminal = classify_planner_terminal_action(
                expected_action=expected_action,
                actual_action="acknowledge_trigger",
                explicit_neutral=neutral_fallback,
            )
            target_status = terminal.status
            terminal_action = terminal.terminal_action
            failure_class = terminal.failure_class
            row = await conn.fetchrow(
                """
                UPDATE plan_delivery_log
                   SET status = $3,
                       acked_at = now(),
                       terminal_action = $4,
                       terminal_at = now(),
                       failure_class = $5,
                       result_payload = jsonb_build_object('reason', $2::text),
                       gateway_body = concat_ws(E'\n', NULLIF(gateway_body, ''), $2::text)
                 WHERE id = $6
                   AND trigger_id = $1::uuid
                   AND status = 'pending'
                 RETURNING id, event_type, instance, delivered_at, status
                """,
                str(tid),
                f"acknowledged by {planner_instance or 'iris'}: {reason}",
                target_status,
                terminal_action,
                failure_class,
                existing["id"],
            )
            if row is None:
                raise RuntimeError("plan delivery attempt lost its acknowledge completion fence")
            if ledger is not None:
                updated_ledger_id = await conn.fetchval(
                    """
                    UPDATE planner_trigger_ledger
                       SET status = $2,
                           terminal_action = $3,
                           terminal_at = now(),
                           failure_class = $4,
                           resolved_at = now(),
                           updated_at = now()
                     WHERE id = $5
                       AND trigger_id = $1::uuid
                       AND plan_delivery_log_id = $6
                       AND status = 'delivered'
                     RETURNING id
                    """,
                    str(tid),
                    target_status,
                    terminal_action,
                    failure_class,
                    ledger["id"],
                    existing["id"],
                )
                if updated_ledger_id != ledger["id"]:
                    raise RuntimeError("planner ledger attempt lost its acknowledge completion fence")
            return _json(
                {
                    "ok": True,
                    "trigger_id": str(tid),
                    "event_type": row["event_type"],
                    "instance": row["instance"],
                    "planner_instance": planner_instance,
                    "status": row["status"],
                    "terminal_action": terminal_action,
                    "neutral": neutral_fallback,
                }
            )
    finally:
        await conn.close()


@mcp.tool()
async def plan_evaluate(plan_id: str, outcome_score: int, actual_outcome: str, lesson_extracted: str = "") -> str:
    """Write the evaluation results for a completed plan back to plan_journal.
    This CLOSES the learning loop: Plan → Execute → Measure → Evaluate → Learn.

    plan_id: the plan to evaluate (e.g. 'iris-20260411-1346')
    outcome_score: 1-10 score for how well the plan achieved its hypothesis
    actual_outcome: what actually happened (stress hours, compliance, key observations)
    lesson_extracted: new lesson learned, or empty if none

    Side effects (loop-closure repair, see migration 111):
      - Computes fn_plan_anchor_score(plan_id), stores it in plan_journal.anchor_score.
      - If |outcome_score - anchor_score| > 2, returns a deviation warning so Iris
        can explain the gap on her next cycle.
      - If lesson_extracted is non-empty, INSERTs a low-confidence planner_lessons
        row in the same transaction (proposed; Iris validates later via lessons_manage).
    """
    try:
        ev = PlanEvaluation.model_validate(
            {
                "plan_id": plan_id,
                "outcome_score": outcome_score,
                "actual_outcome": actual_outcome,
                "lesson_extracted": lesson_extracted or None,
            }
        )
    except ValidationError as e:
        return json.dumps({"error": "PlanEvaluation validation failed", "details": json.loads(e.json())})

    conn = await _db()
    try:
        existing = await conn.fetchrow(
            "SELECT plan_id, hypothesis_structured FROM plan_journal WHERE plan_id = $1",
            ev.plan_id,
        )
        if not existing:
            return json.dumps({"error": f"Plan '{ev.plan_id}' not found in plan_journal"})

        async with conn.transaction():
            anchor_row = await conn.fetchrow("SELECT fn_plan_anchor_score($1) AS anchor", ev.plan_id)
            anchor_score = anchor_row["anchor"] if anchor_row else None
            guardrail_row = await conn.fetchrow(
                """
                SELECT guardrail_events, held_guardrail_events,
                       dispatched_guardrail_events, vpd_high_guardrail_events,
                       guardrail_penalty
                  FROM v_plan_guardrail_scorecard
                 WHERE plan_id = $1
                """,
                ev.plan_id,
            )
            # Persist the guardrail penalty alongside the outcome/anchor scores.
            # It was fetched (and surfaced in the return payload) but never
            # written back, so v_plan_guardrail_scorecard's signal could not
            # feed the reward swap (migration 147) or any downstream learning
            # query reading plan_journal directly. Default to 0 (no clamps in
            # the governed interval) when the scorecard has no row yet.
            guardrail_penalty = guardrail_row["guardrail_penalty"] if guardrail_row else 0

            await conn.execute(
                """UPDATE plan_journal SET
                    outcome_score = $2, actual_outcome = $3, lesson_extracted = $4,
                    anchor_score  = $5, guardrail_penalty = $6, validated_at = now()
                   WHERE plan_id = $1""",
                ev.plan_id,
                ev.outcome_score,
                ev.actual_outcome,
                ev.lesson_extracted,
                anchor_score,
                guardrail_penalty,
            )

            lesson_row_id = None
            if ev.lesson_extracted:
                # Lessonization (Phase 2a): convert lesson_extracted text into a
                # queryable planner_lessons row. Category derived from the plan's
                # dominant stress type during its governed interval; condition
                # derived from hypothesis_structured.conditions when present,
                # else a templated description.
                cat_row = await conn.fetchrow(
                    """
                    SELECT CASE
                             WHEN heat_stress_h     >= GREATEST(cold_stress_h, vpd_high_stress_h, vpd_low_stress_h)
                               THEN 'cooling'
                             WHEN cold_stress_h     >= GREATEST(heat_stress_h, vpd_high_stress_h, vpd_low_stress_h)
                               THEN 'heating'
                             WHEN vpd_high_stress_h >= GREATEST(heat_stress_h, cold_stress_h, vpd_low_stress_h)
                               THEN 'misting'
                             WHEN vpd_low_stress_h  >= GREATEST(heat_stress_h, cold_stress_h, vpd_high_stress_h)
                               THEN 'humidity'
                             ELSE 'planning'
                           END AS category
                      FROM v_plan_window_scorecard WHERE plan_id = $1
                    """,
                    ev.plan_id,
                )
                category = (cat_row["category"] if cat_row else None) or "planning"

                hs = existing["hypothesis_structured"]
                if hs and isinstance(hs, dict) and hs.get("conditions"):
                    c = hs["conditions"]
                    condition = (
                        f"outdoor_high={c.get('outdoor_temp_peak_f', '?')}F, "
                        f"outdoor_rh_min={c.get('outdoor_rh_min_pct', '?')}%, "
                        f"solar_peak={c.get('solar_peak_w_m2', '?')} W/m^2"
                    )
                else:
                    condition = f"auto-extracted from {ev.plan_id}"

                lesson_row = await conn.fetchrow(
                    """
                    INSERT INTO planner_lessons
                      (category, condition, lesson, confidence, times_validated,
                       source_plan_ids, is_active, greenhouse_id)
                    VALUES ($1, $2, $3, 'low', 1, ARRAY[$4]::text[], true, 'vallery')
                    RETURNING id
                    """,
                    category,
                    condition,
                    ev.lesson_extracted,
                    ev.plan_id,
                )
                lesson_row_id = lesson_row["id"] if lesson_row else None

        deviation = abs(ev.outcome_score - anchor_score) if anchor_score is not None else None
        warning = None
        if deviation is not None and deviation > 2:
            direction = "high" if ev.outcome_score > anchor_score else "low"
            warning = (
                f"Self-score {ev.outcome_score} deviates from deterministic anchor "
                f"{anchor_score} by {deviation} ({direction}). Explain the gap on "
                f"the next cycle, or revise your grade."
            )

        site_publish_triggered = False
        try:
            if SITE_PUBLISH_TRIGGER_PATH:
                trigger_path = Path(SITE_PUBLISH_TRIGGER_PATH)
                trigger_path.parent.mkdir(parents=True, exist_ok=True)
                trigger_path.write_text(f"evaluation:{ev.plan_id}\n{datetime.now(ZoneInfo('UTC')).isoformat()}\n")
                site_publish_triggered = True
        except Exception as e:  # never block evaluation persistence on publish trigger failures
            print(f"plan-evaluate publish trigger write failed (non-fatal): {e}")

        return json.dumps(
            {
                "ok": True,
                "plan_id": ev.plan_id,
                "outcome_score": ev.outcome_score,
                "anchor_score": anchor_score,
                "guardrail_scorecard": dict(guardrail_row) if guardrail_row else None,
                "deviation_warning": warning,
                "lesson_row_id": lesson_row_id,
                "site_publish_triggered": site_publish_triggered,
            }
        )
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# HISTORY TOOL
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def history(metric: str = "climate", hours: int = 24, resolution_min: int = 15) -> str:
    """Get historical time-bucketed data for any sensor domain.
    metric: 'climate' (temp, vpd, rh, dew_point), 'equipment' (relay state durations),
            'energy' (power watts), 'outdoor' (weather station), 'diagnostics' (ESP32 health)
    hours: lookback window (default 24)
    resolution_min: bucket size in minutes (default 15)
    Returns JSON array of time-bucketed records."""
    queries = {
        "climate": """
            SELECT time_bucket($1::interval, ts) AS time,
                   round(avg(temp_avg)::numeric, 1) AS temp_f,
                   round(avg(vpd_avg)::numeric, 2) AS vpd_kpa,
                   round(avg(rh_avg)::numeric, 0) AS rh_pct,
                   round(avg(dew_point)::numeric, 1) AS dew_point_f,
                   round(avg(outdoor_temp_f)::numeric, 1) AS outdoor_temp,
                   round(avg(outdoor_rh_pct)::numeric, 0) AS outdoor_rh
            FROM climate WHERE ts > now() - $2::interval AND temp_avg IS NOT NULL
            GROUP BY 1 ORDER BY 1""",
        "energy": """
            SELECT time_bucket($1::interval, ts) AS time,
                   round(avg(watts_total)::numeric, 0) AS watts
            FROM energy WHERE ts > now() - $2::interval
            GROUP BY 1 ORDER BY 1""",
        "outdoor": """
            SELECT time_bucket($1::interval, ts) AS time,
                   round(avg(outdoor_temp_f)::numeric, 1) AS temp_f,
                   round(avg(outdoor_rh_pct)::numeric, 0) AS rh_pct,
                   round(avg(solar_irradiance_w_m2)::numeric, 0) AS solar_w
            FROM climate WHERE ts > now() - $2::interval AND outdoor_temp_f IS NOT NULL
            GROUP BY 1 ORDER BY 1""",
        "equipment": """
            SELECT time_bucket($1::interval, e.ts) AS time,
                   e.equipment,
                   round(sum(CASE WHEN e.state THEN 1.0 ELSE 0.0 END) / count(*)::numeric * 100, 0) AS on_pct
            FROM equipment_state e
            WHERE e.ts > now() - $2::interval
              AND e.equipment IN ('fan1','fan2','vent','fog','heat1','heat2','mister_south','mister_west','mister_center')
            GROUP BY 1, 2 ORDER BY 1, 2""",
        "diagnostics": """
            SELECT time_bucket($1::interval, ts) AS time,
                   round(avg(wifi_rssi)::numeric, 0) AS wifi_rssi,
                   round(avg(heap_bytes)::numeric, 0) AS heap_bytes,
                   round(max(uptime_s)::numeric, 0) AS uptime_s
            FROM diagnostics WHERE ts > now() - $2::interval
            GROUP BY 1 ORDER BY 1""",
    }

    template = queries.get(metric)
    if not template:
        return json.dumps({"error": f"Unknown metric '{metric}'. Use: {', '.join(queries.keys())}"})

    # Inline interval values (asyncpg needs timedelta for interval params)
    sql = template.replace("$1::interval", f"'{resolution_min} minutes'::interval").replace(
        "$2::interval", f"'{hours} hours'::interval"
    )

    conn = await _db()
    try:
        rows = await conn.fetch(sql)
        return _json([dict(r) for r in rows[:500]])
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# CROP MANAGEMENT TOOLS
# ═══════════════════════════════════════════════════════════════


def _crops_legacy_write_refusal() -> str | None:
    """Iris stays read-only on plantings (#1328): its crops create and deactivate are refused."""
    caller = current_actor()
    if caller is None or caller.source == "iris":
        return json.dumps(
            {
                "error": "Iris does not record plantings; a person (dashboard Crops) or a named agent does (record_planting)"
            }
        )
    return None


async def _crops_legacy_write(write) -> str:
    """The crops tool's create and deactivate through verdify_services.crops, answered in the tool's old JSON
    style: the planting, or {"error", "problem"}."""
    caller = current_actor()
    actor = ServiceActor(source=caller.source, name=caller.name)
    conn = await _db()
    try:
        result = await write(conn, actor)
    except ServiceError as refused:
        return json.dumps({"error": refused.problem.detail, "problem": refused.problem.body()})
    finally:
        await conn.close()
    return json.dumps(result.value.model_dump(mode="json"))


@mcp.tool()
async def crops(action: str, crop_id: int = 0, data: str = "") -> str:
    """Manage greenhouse crops. Actions: list, get, create, update, deactivate.
    list: all active crops with zone, stage, recent health
    get: full detail for one crop including its nutrient recipe(s), observations,
         and events. For the Vanda (crop_id 5) this surfaces `vanda_orchid_active`
         (MSU 13-3-15, 50 ppm N, ec 0.40 ABSOLUTE on RO; is_active=FALSE until the
         operator confirms it). Dose to target_ec — NOT 2-part A/B ml/L math (SAF-2).
    create: data = {"name", "variety", "zone", "position", "planted_date", "stage", ...}
    update: data = {"name"?, "stage"?, "zone"?, "expected_harvest"?, "notes"?, ...}
    deactivate: soft-delete by crop_id"""
    d = json.loads(data) if data else {}
    conn = await _db()
    try:
        if action == "list":
            rows = await conn.fetch("""
                SELECT c.id, c.name, c.variety, c.zone, c.position, c.stage, c.planted_date,
                       c.expected_harvest, c.is_active,
                       (SELECT round(avg(health_score)::numeric, 2) FROM observations o
                        WHERE o.crop_id = c.id AND o.health_score IS NOT NULL
                        AND o.ts > now() - interval '7 days') AS health_7d
                FROM crops c WHERE c.is_active = true AND c.greenhouse_id = 'vallery'
                ORDER BY c.zone, c.position""")
            return _json([dict(r) for r in rows])

        elif action == "get" and crop_id:
            row = await conn.fetchrow("SELECT * FROM crops WHERE id = $1", crop_id)
            if not row:
                return json.dumps({"error": f"Crop {crop_id} not found"})
            obs = await conn.fetch(
                "SELECT ts, obs_type, notes, health_score FROM observations WHERE crop_id = $1 ORDER BY ts DESC LIMIT 10",
                crop_id,
            )
            events = await conn.fetch(
                "SELECT ts, event_type, old_stage, new_stage, notes FROM crop_events WHERE crop_id = $1 ORDER BY ts DESC LIMIT 10",
                crop_id,
            )
            # N1 (FRT-1): surface this crop's nutrient recipe(s) so the planner can
            # reason about feed without blind A/B dose math. For the Vanda (crop_id 5)
            # this exposes `vanda_orchid_active` (MSU 13-3-15, 50 ppm N, ec 0.40
            # ABSOLUTE on RO). The recipe ships is_active=FALSE until the operator
            # confirms it physically — `is_active` tells the planner whether the feed
            # is live or provisional; the notes carry the SAF-2 dosing guardrails
            # (single-salt, dose-to-EC not A/B ml/L, AM-only, absorption hold).
            recipes = await conn.fetch(
                """SELECT name, stage, is_active, target_ec, target_ph_low, target_ph_high,
                          n_ppm, p_ppm, k_ppm, ca_ppm, mg_ppm, fe_ppm,
                          stock_a_ml_per_l, stock_b_ml_per_l, notes
                     FROM nutrient_recipes WHERE crop_id = $1 ORDER BY is_active DESC, name""",
                crop_id,
            )
            return _json(
                {
                    "crop": dict(row),
                    "nutrient_recipes": [dict(r) for r in recipes],
                    "recent_observations": [dict(o) for o in obs],
                    "recent_events": [dict(e) for e in events],
                }
            )

        elif action == "create":
            # #1328: create goes through the planting service (record_planting), so a planting always occupies its
            # place and is attributed; the old INSERT dropped position_id and left the row nowhere.
            try:
                payload = CropCreate.model_validate(d)
            except ValidationError as e:
                return json.dumps({"error": "CropCreate validation failed", "details": json.loads(e.json())})
            refused = _crops_legacy_write_refusal()
            if refused:
                return refused
            control = sorted(
                name
                for name in ("target_dli", "target_vpd_low", "target_vpd_high", "base_temp_f")
                if name in payload.model_fields_set
            )
            if control:
                return json.dumps(
                    {
                        "error": "Plantings are records only; these fields set control targets and are not taken here",
                        "fields": control,
                    }
                )
            place = payload.position_label or payload.position
            if payload.position_id is not None:
                place = await conn.fetchval("SELECT label FROM positions WHERE id = $1", payload.position_id) or place
            body = {
                "crop": payload.crop_catalog_slug or payload.name,
                "variety": payload.variety,
                "positions": [place],
                "count": payload.count,
                "planted_at": payload.planted_date.isoformat(),
                "stage": payload.stage,
                "notes": payload.notes,
            }
            return await _crops_legacy_write(lambda c, actor: crop_service.plant(c, actor, body))

        elif action == "update" and crop_id:
            try:
                patch = CropUpdate.model_validate(d)
            except ValidationError as e:
                return json.dumps({"error": "CropUpdate validation failed", "details": json.loads(e.json())})
            set_fields = patch.model_dump(exclude_unset=True)
            if not set_fields:
                return json.dumps({"error": "No fields to update"})
            moving = sorted(set(set_fields) & {"zone", "position", "position_id", "zone_id"})
            if moving:
                return json.dumps(
                    {
                        "error": "A planting's place changes with move_planting, which keeps its history",
                        "fields": moving,
                    }
                )
            sets = [f"{k} = ${i}" for i, k in enumerate(set_fields, start=2)]
            sets.append("updated_at = now()")
            vals = [crop_id, *set_fields.values()]
            row = await conn.fetchrow(f"UPDATE crops SET {', '.join(sets)} WHERE id = $1 RETURNING *", *vals)
            return _json(dict(row)) if row else json.dumps({"error": "Crop not found"})

        elif action == "deactivate" and crop_id:
            # #1328: ends the planting through the service, so its places close and it is attributed.
            refused = _crops_legacy_write_refusal()
            if refused:
                return refused
            body = {"end_reason": "removed", "reason": d.get("reason")}
            return await _crops_legacy_write(lambda c, actor: crop_service.end(c, actor, crop_id, body))

        return json.dumps({"error": f"Unknown action '{action}'. Use: list, get, create, update, deactivate"})
    finally:
        await conn.close()


@mcp.tool()
async def observations(action: str, crop_id: int = 0, data: str = "") -> str:
    """Record and query crop observations, events, harvests, and treatments.
    Actions: list_observations, record_observation, list_events, record_event,
             record_harvest, list_harvests, record_treatment, list_treatments.
    data: JSON with fields appropriate to the action. Envelopes:
      record_observation -> ObservationCreate (obs_type, notes, severity, ...)
      record_event       -> EventCreate (event_type, old_stage, new_stage, ...)
      record_harvest     -> HarvestCreate (weight_kg, unit_count, quality_grade,
                            zone, destination, unit_price, revenue, operator, notes)
      record_treatment   -> TreatmentCreate (product, active_ingredient,
                            concentration, rate, rate_unit, method, zone,
                            target_pest, phi_days, rei_hours, applicator,
                            observation_id, followup_due_at,
                            followup_completed_at, outcome, notes)"""
    d = json.loads(data) if data else {}
    conn = await _db()
    try:
        if action == "list_observations":
            rows = await conn.fetch(
                """
                SELECT o.id, o.ts, o.greenhouse_id, o.crop_id, c.name,
                       o.position_id, o.zone_id, o.zone, o.position, o.obs_type,
                       o.notes, o.health_score, o.severity, o.observer
                FROM observations o
                JOIN crops c ON o.crop_id = c.id AND c.greenhouse_id = 'vallery'
                WHERE o.greenhouse_id = 'vallery'
                  AND ($1::int = 0 OR o.crop_id = $1)
                ORDER BY o.ts DESC LIMIT 50""",
                crop_id,
            )
            return _json([dict(r) for r in rows])

        elif action == "record_observation" and crop_id:
            crop = await conn.fetchrow(
                "SELECT zone, position, zone_id, position_id FROM crops WHERE id = $1 AND greenhouse_id = 'vallery'",
                crop_id,
            )
            if not crop:
                return json.dumps({"error": f"Crop {crop_id} not found"})
            try:
                obs = ObservationCreate.model_validate({**d, "observer": d.get("observer") or "Iris"})
            except ValidationError as e:
                return json.dumps({"error": "ObservationCreate validation failed", "details": json.loads(e.json())})
            row = await conn.fetchrow(
                """
                INSERT INTO observations (
                    crop_id, greenhouse_id, zone, position, zone_id, position_id, obs_type, notes, severity,
                    observer, health_score, species, count, affected_pct, photo_path,
                    plant_height_cm, leaf_count, canopy_cover_pct, flowering_count,
                    fruit_count, root_condition, mortality_count, stress_tags, source
                )
                VALUES (
                    $1, 'vallery', $2, $3, $4, $5, $6, $7, $8,
                    $9, $10, $11, $12, $13, $14,
                    $15, $16, $17, $18, $19, $20, $21, $22, 'iris'
                ) RETURNING *""",
                crop_id,
                obs.zone or crop["zone"],
                obs.position or crop["position"],
                crop["zone_id"],
                crop["position_id"],
                obs.obs_type,
                obs.notes,
                obs.severity,
                obs.observer,
                obs.health_score,
                obs.species,
                obs.count,
                obs.affected_pct,
                obs.photo_path,
                obs.plant_height_cm,
                obs.leaf_count,
                obs.canopy_cover_pct,
                obs.flowering_count,
                obs.fruit_count,
                obs.root_condition,
                obs.mortality_count,
                obs.stress_tags,
            )
            return _json(dict(row))

        elif action == "list_events":
            rows = await conn.fetch(
                """
                SELECT e.id, e.ts, e.greenhouse_id, e.crop_id, c.name,
                       e.position_id, e.event_type, e.old_stage, e.new_stage,
                       e.count, e.operator, e.source, e.notes
                FROM crop_events e
                JOIN crops c ON e.crop_id = c.id AND c.greenhouse_id = 'vallery'
                WHERE e.greenhouse_id = 'vallery'
                  AND ($1::int = 0 OR e.crop_id = $1)
                ORDER BY e.ts DESC LIMIT 50""",
                crop_id,
            )
            return _json([dict(r) for r in rows])

        elif action == "record_event" and crop_id:
            crop = await conn.fetchrow(
                "SELECT position_id FROM crops WHERE id = $1 AND greenhouse_id = 'vallery'",
                crop_id,
            )
            if not crop:
                return json.dumps({"error": f"Crop {crop_id} not found"})
            try:
                ev = EventCreate.model_validate({**d, "operator": d.get("operator") or "Iris"})
            except ValidationError as e:
                return json.dumps({"error": "EventCreate validation failed", "details": json.loads(e.json())})
            row = await conn.fetchrow(
                """
                INSERT INTO crop_events (
                    crop_id, greenhouse_id, position_id, event_type, old_stage,
                    new_stage, count, operator, source, notes
                )
                VALUES ($1, 'vallery', $2, $3, $4, $5, $6, $7, 'iris', $8) RETURNING *""",
                crop_id,
                crop["position_id"],
                ev.event_type,
                ev.old_stage,
                ev.new_stage,
                ev.count,
                ev.operator,
                ev.notes,
            )
            return _json(dict(row))

        elif action == "record_harvest" and crop_id:
            crop = await conn.fetchrow(
                "SELECT zone, position_id FROM crops WHERE id = $1 AND greenhouse_id = 'vallery'",
                crop_id,
            )
            if not crop:
                return json.dumps({"error": f"Crop {crop_id} not found"})
            try:
                hv = HarvestCreate.model_validate({**d, "operator": d.get("operator") or "Iris"})
            except ValidationError as e:
                return json.dumps({"error": "HarvestCreate validation failed", "details": json.loads(e.json())})
            row = await conn.fetchrow(
                """
                INSERT INTO harvests (
                    crop_id, weight_kg, unit_count, quality_grade,
                    salable_weight_kg, cull_weight_kg, cull_reason, quality_reason,
                    zone, destination, unit_price, revenue, labor_minutes, operator,
                    position_id, greenhouse_id, notes
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, 'vallery', $16)
                RETURNING *""",
                crop_id,
                hv.weight_kg,
                hv.unit_count,
                hv.quality_grade,
                hv.salable_weight_kg,
                hv.cull_weight_kg,
                hv.cull_reason,
                hv.quality_reason,
                hv.zone or crop["zone"],
                hv.destination,
                hv.unit_price,
                hv.revenue,
                hv.labor_minutes,
                hv.operator,
                crop["position_id"],
                hv.notes,
            )
            return _json(dict(row))

        elif action == "list_harvests":
            rows = await conn.fetch(
                """
                SELECT h.id, h.ts, h.greenhouse_id, h.crop_id, c.name, h.position_id,
                       h.weight_kg, h.unit_count, h.quality_grade,
                       h.salable_weight_kg, h.cull_weight_kg, h.cull_reason,
                       h.quality_reason, h.zone, h.destination, h.unit_price,
                       h.revenue, h.labor_minutes, h.operator, h.notes
                FROM harvests h JOIN crops c ON h.crop_id = c.id
                WHERE h.greenhouse_id = 'vallery'
                  AND c.greenhouse_id = 'vallery'
                  AND ($1::int = 0 OR h.crop_id = $1)
                ORDER BY h.ts DESC LIMIT 50""",
                crop_id,
            )
            return _json([dict(r) for r in rows])

        elif action == "record_treatment" and crop_id:
            crop = await conn.fetchrow(
                "SELECT zone, position_id FROM crops WHERE id = $1 AND greenhouse_id = 'vallery'",
                crop_id,
            )
            if not crop:
                return json.dumps({"error": f"Crop {crop_id} not found"})
            try:
                tr = TreatmentCreate.model_validate({**d, "applicator": d.get("applicator") or "Iris"})
            except ValidationError as e:
                return json.dumps({"error": "TreatmentCreate validation failed", "details": json.loads(e.json())})
            row = await conn.fetchrow(
                """
                INSERT INTO treatments (
                    crop_id, product, active_ingredient, concentration, rate, rate_unit,
                    method, zone, target_pest, phi_days, rei_hours, applicator,
                    observation_id, position_id, greenhouse_id, followup_due_at,
                    followup_completed_at, outcome, notes
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, 'vallery', $15, $16, $17, $18)
                RETURNING *""",
                crop_id,
                tr.product,
                tr.active_ingredient,
                tr.concentration,
                tr.rate,
                tr.rate_unit,
                tr.method,
                tr.zone or crop["zone"],
                tr.target_pest,
                tr.phi_days,
                tr.rei_hours,
                tr.applicator,
                tr.observation_id,
                crop["position_id"],
                tr.followup_due_at,
                tr.followup_completed_at,
                tr.outcome,
                tr.notes,
            )
            return _json(dict(row))

        elif action == "list_treatments":
            rows = await conn.fetch(
                """
                SELECT t.id, t.ts, t.greenhouse_id, t.crop_id, c.name, t.position_id,
                       t.product, t.active_ingredient, t.concentration, t.rate,
                       t.rate_unit, t.method, t.zone, t.target_pest, t.phi_days,
                       t.rei_hours, t.applicator, t.observation_id,
                       t.followup_due_at, t.followup_completed_at, t.outcome,
                       t.notes
                FROM treatments t JOIN crops c ON t.crop_id = c.id
                WHERE t.greenhouse_id = 'vallery'
                  AND c.greenhouse_id = 'vallery'
                  AND ($1::int = 0 OR t.crop_id = $1)
                ORDER BY t.ts DESC LIMIT 50""",
                crop_id,
            )
            return _json([dict(r) for r in rows])

        return json.dumps({"error": f"Unknown action '{action}'"})
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# ALERT MANAGEMENT
# ═══════════════════════════════════════════════════════════════


def _alert_row(alert: alerts_service.Alert) -> dict[str, Any]:
    """A merged alert in the tool's row shape. ``acknowledged`` is false exactly while the merged status is open
    (a resolved row needs no acknowledgement), so the unacknowledged rows equal ``alerts.open_count()``."""
    seen = alert.last_seen_at or alert.first_seen_at
    return {
        "id": alert.id,
        "time": seen.astimezone(ZoneInfo("America/Denver")).strftime("%m-%d %H:%M"),
        "alert_type": alert.alert_type,
        "severity": alert.severity,
        "message": alert.message,
        "disposition": alert.status,
        "acknowledged": alert.status != "open",
        "resolved": alert.status == "resolved",
        "count": alert.count,
    }


async def _recent_alert_rows(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    """Every page of ``alerts.list(status="recent")``: every open alert plus the last 24 h of resolved ones."""
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        page = await alerts_service.list(conn, status="recent", limit=alerts_service.LIMIT_MAX, cursor=cursor)
        rows.extend(_alert_row(alert) for alert in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return rows


def _alert_refusal(alert_id: int, refused: ServiceError) -> str:
    if refused.problem.code == "unknown_target":
        return json.dumps({"error": f"alert_id {alert_id} not found"})
    return json.dumps({"error": refused.problem.detail})


@mcp.tool()
async def alerts(action: str = "list", alert_id: int = 0, data: str = "") -> str:
    """Manage greenhouse alerts. Actions: list, acknowledge, resolve.
    list: every open alert plus those resolved in the last 24 h, repeats merged into one row with their count;
    disposition is the merged status and the unacknowledged rows are the open-alert count
    acknowledge: mark every repeat as seen
    resolve: close every repeat with resolution notes (data can be JSON or plain text)
    acknowledge and resolve are recorded as the calling credential (iris,
    agent:<name> or agent:admin); acknowledged_by and resolved_by in data are ignored."""
    d: dict[str, Any] = {}
    if data:
        try:
            parsed = json.loads(data)
        except ValueError:
            parsed = None
        d = parsed if isinstance(parsed, dict) else {"resolution": data}
    conn = await _db()
    try:
        if action == "list":
            return _json(await _recent_alert_rows(conn))

        elif action == "acknowledge" and alert_id:
            # VD-59: the actor comes only from the request, never from data.
            actor = _service_actor()
            if actor is None:
                return json.dumps({"error": "forbidden: no resolved caller"})
            try:
                acknowledged = await alerts_service.ack(conn, actor, alert_id, None, None)
            except ServiceError as refused:
                return _alert_refusal(alert_id, refused)
            # ack touches only rows with resolved_at IS NULL: a resolved row's merged row reads resolved.
            done = "already_resolved" if acknowledged.value.status == "resolved" else "acknowledged"
            return json.dumps({"ok": True, "alert_id": alert_id, "action": done})

        elif action == "resolve" and alert_id:
            # alert_log.id is an integer, so an id beyond it is simply unknown, as the service answers.
            dependency = (
                await conn.fetchrow("SELECT alert_type, source FROM alert_log WHERE id=$1", alert_id)
                if abs(alert_id) < 2**31
                else None
            )
            if (
                dependency
                and dependency["alert_type"] == "planner_tool_dependency_failed"
                and dependency["source"] == "mcp"
            ):
                return json.dumps(
                    {"error": "semantic dependency alerts resolve only after successful same-tool retrieval"}
                )
            # VD-59: after the retrieval check, so a semantic dependency alert
            # keeps its own refusal; the actor never comes from data.
            actor = _service_actor()
            if actor is None:
                return json.dumps({"error": "forbidden: no resolved caller"})
            default_resolution = "Resolved by Iris" if actor.source == "iris" else f"Resolved by {actor.source}"
            try:
                res = AlertResolvePayload.model_validate(
                    {
                        "resolved_by": actor.source,
                        "resolution": d.get("resolution") or default_resolution,
                    }
                )
            except ValidationError as e:
                return json.dumps({"error": "AlertResolvePayload validation failed", "details": json.loads(e.json())})
            try:
                await alerts_service.resolve(conn, actor, alert_id, res.resolution, None)
            except ServiceError as refused:
                return _alert_refusal(alert_id, refused)
            return json.dumps({"ok": True, "alert_id": alert_id, "action": "resolved"})

        return json.dumps({"error": f"Unknown action '{action}'. Use: list, acknowledge, resolve"})
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# SETTINGS AND ACTIVITY (architecture §5.6)
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
@_answers_problems
async def set_setting(
    param: Annotated[str, Field(description="The setting's registry name, as the settings tool lists it.")],
    value: Annotated[
        float | None, Field(description="The value to store, in the setting's unit. Omit it with release=true.")
    ] = None,
    release: Annotated[
        bool, Field(description="True, with no value: release the setting to its plan, band or default.")
    ] = False,
    manual: Annotated[
        bool, Field(description="Hold the value manual: Iris's plan values for it lose. Stored as passed.")
    ] = False,
    reason: Annotated[str | None, Field(description="Why, at most 500 characters.")] = None,
    request_id: Annotated[
        str | None, Field(description="A UUID you generate; a retry with the same one and payload changes nothing.")
    ] = None,
    trigger_id: Annotated[
        str | None, Field(description="Optional planning trigger UUID, kept only in the activity log.")
    ] = None,
    planner_instance: Annotated[
        Literal["local", "opus"] | None, Field(description="Optional planner instance, kept only in the activity log.")
    ] = None,
):
    """Change any writable setting within registry min/max, or release it. No trigger is needed.

    REST twins: API-10 `PUT /v1/settings/{param}` (a write: pass value) and API-11
    `DELETE /v1/settings/{param}` (a release: release=true and no value). Any other combination is
    `invalid_request`. The change reaches the controller through the dispatcher's next pass.

    manual defaults to false here; a write stores the flag it carries, so writing a manual setting without
    manual=true clears it (manual_before and the activity log say so).

    Returns the API-10 DesiredSetting: the snapped value, snapped_from when snapping changed it, manual,
    manual_before and state `requested`. The same request_id and payload return the stored JSON with a top-level
    replayed: true and append nothing.

    Refusals are Problem JSON with isError: `unknown_target`; `read_only` with the reason; `out_of_range` with min,
    max and current; `manual` when Iris writes or releases a setting whose newest row is manual (a person's value
    stays; other agents' writes win); `request_id_reused` when the same request_id carries another payload;
    `invalid_request` when request_id or trigger_id is not a UUID; `forbidden` when the call has no resolved caller.

    trigger_id and planner_instance only annotate the activity log; it does not count as your plan for a trigger:
    Iris still ends every trigger with set_plan or acknowledge_trigger."""
    actor = _service_actor()
    if actor is None:
        raise ServiceError(problem("forbidden", "set_setting needs a resolved caller"))
    if release:
        if value is not None:
            raise ServiceError(problem("invalid_request", "A release takes no value", field="value"))
        if manual:
            raise ServiceError(problem("invalid_request", "A release cannot be manual", field="manual"))
    elif value is None:
        raise ServiceError(problem("invalid_request", "Pass value, or release=true with no value", field="value"))
    request_key = _uuid_argument("request_id", request_id)
    trigger = _uuid_argument("trigger_id", trigger_id)
    # Only the activity detail: nothing here reads, locks or updates a planner delivery or trigger ledger row.
    extra_detail = {
        name: audit
        for name, audit in (("trigger_id", trigger), ("planner_instance", planner_instance))
        if audit is not None
    }
    async with _service_connection() as conn:
        if release:
            result = await settings_service.release(conn, actor, param, reason, request_key, extra_detail=extra_detail)
        else:
            result = await settings_service.write(
                conn, actor, param, value, manual, reason, request_key, extra_detail=extra_detail
            )
    return _tool_ok(result.value, replayed=result.replayed)


def _setting_selected(
    setting: settings_service.Setting,
    param: str | None,
    group: str | None,
    writable_only: bool,
    manual_only: bool,
) -> bool:
    return (
        (param is None or setting.param == param)
        and (group is None or setting.group == group)
        and (not writable_only or setting.writable)
        and (not manual_only or (setting.desired is not None and setting.desired.manual))
    )


@mcp.tool()
@_answers_problems
async def settings(
    param: Annotated[str | None, Field(description="One setting's registry name.")] = None,
    group: Annotated[
        settings_service.SettingGroup | None, Field(description="Only the rows of this Setting.group.")
    ] = None,
    writable_only: Annotated[bool, Field(description="Only the rows a write can change now.")] = False,
    manual_only: Annotated[bool, Field(description="Only the rows whose newest desired row is manual.")] = False,
    since: Annotated[
        str | None, Field(description="History start, ISO-8601 with its UTC offset (API-12's from).")
    ] = None,
    until: Annotated[str | None, Field(description="History end, ISO-8601 with its UTC offset (API-12's to).")] = None,
):
    """Read the settings catalog, or one setting's history.

    REST twins: API-09 `GET /v1/settings` and API-12 `GET /v1/settings/{param}/history`.

    With no window: API-09's `{items, server_time}`, filtered by param, group, writable_only and manual_only. Each
    row carries applied_from (whose value is in force: manual, desired, plan, band or default), min, max, step,
    its desired row, its plan value and its readback state, and `help`: what the setting is (summary), what the
    controller does with it (effect), what raising and lowering it does (raise, lower; for a switch, turning it on and
    off) and any caution, the same words the dashboard shows; a guardrail row has guardrail true. A read-only row
    carries read_only_kind, and a derived row source_params, the writable settings it is computed from. Explain a
    change with that text. Narrow it: the whole catalog is large.

    With param, since and until all given: API-12's SettingHistory over [since, until), the desired rows, plan
    values and readback change points; since and until map to API-12's from and to, and the other filters do not
    apply. since or until without the other two is `invalid_request`. An unknown param in a history read is
    `not_found`. Refusals are Problem JSON with isError."""
    if since is None and until is None:
        async with _service_connection() as conn:
            rows = await reads_service.settings_rows(conn)
        items = [row for row in rows.items if _setting_selected(row, param, group, writable_only, manual_only)]
        return _tool_ok(rows.model_copy(update={"items": items}))
    if param is None or since is None or until is None:
        raise ServiceError(
            problem(
                "invalid_request",
                "A history read needs param, since and until together",
                field="since" if since is None else "until" if until is None else "param",
            )
        )
    from_, to = _time_argument("since", since), _time_argument("until", until)
    async with _service_connection() as conn:
        return _tool_ok(await reads_service.setting_history(conn, param, from_, to))


@mcp.tool()
@_answers_problems
async def activity(
    source: Annotated[
        str | None, Field(description="dashboard, iris, system, agent (every agent) or agent:<name>.")
    ] = None,
    actor: Annotated[str | None, Field(description="The actor's exact name.")] = None,
    action: Annotated[str | None, Field(description="A prefix, such as setting.write or irrigation.")] = None,
    target: Annotated[str | None, Field(description="The exact target: a setting, plan id, alert id or line.")] = None,
    since: Annotated[
        str | None, Field(description="Oldest time, ISO-8601 with its UTC offset (API-17's from).")
    ] = None,
    limit: Annotated[int, Field(description="Rows per page, 1 to 200.")] = activity_service.LIMIT_DEFAULT,
    cursor: Annotated[str | None, Field(description="The previous page's next_cursor.")] = None,
):
    """Read every write by people, Iris, agents and the system, newest first: what happened to your writes and who
    changed the same target since.

    REST twin: API-17 `GET /v1/activity`. Returns ActivityPage `{items, next_cursor}`; each setting row carries its
    readback state and confirmed_at. since maps to API-17's from. Narrow it: a plan.write row lists every row its
    plan wrote, up to hundreds. A bad filter, limit, cursor or time is `invalid_request` as Problem JSON with
    isError."""
    from_ = _time_argument("since", since)
    async with _service_connection() as conn:
        return _tool_ok(
            await reads_service.activity_page(
                conn,
                source=source,
                actor=actor,
                action=action,
                target=target,
                from_=from_,
                limit=limit,
                cursor=cursor,
            )
        )


# ── The house band (#1334): API-23's read, never its Save ──
# The band is operator-owned (decisions 2026-11-05): there is no band write tool, and Iris is not in these reads'
# audience. API-24 (PUT /v1/band/profile) stays the dashboard's.

_BAND_DAY_DESCRIPTION = "America/Denver day, YYYY-MM-DD, at most 400 days from today; default today."


def _band_day(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ServiceError(problem("invalid_request", "day is a local date, YYYY-MM-DD", field="day")) from exc


def _band_temp(description: str, minimum: float | None = None):
    return Field(default=None, description=f"{description}, °F.", ge=minimum)


def _band_vpd(description: str, minimum: float | None = None):
    return Field(default=None, description=f"{description}, kPa.", ge=minimum)


@mcp.tool()
@_answers_problems
async def band_profile(day: Annotated[str | None, Field(description=_BAND_DAY_DESCRIPTION)] = None):
    """Read the house band: its profile, its 24 anchors and its curve over one day. REST twin: API-23
    (GET /v1/band/profile) with no draft; the same JSON (BandProfilePreview).

    It only reads. Returns current (profile: day and night targets, the temperature ± widths and the VPD range below
    and above; the 24 raw anchors; simple), the day's curve at 10-min steps of t (temp and vpd low, target and high,
    as the controller evaluates them), vpd.device (with the night_vpd_bias_kpa the controller reports running), the
    sun times, the guardrails, overshoot, the night heat estimate, the expected night VPD ribbon and its fit, findings,
    measured (the last 24 h of house temp_avg and vpd_avg on t by local clock time: today's before now, yesterday's
    after) and forecast (the day's outdoor temperature and VPD). draft equals current and changed is empty. Unknown
    values are null, never 0.

    There is no band write tool: the band is operator-owned and is saved on the dashboard (Control → Band). Use
    band_profile_preview to evaluate a change. Refusals return the Problem JSON with isError: invalid_request (day),
    read_only (the house band has no complete anchor set), query_timeout and unavailable (both retryable)."""
    from verdify_services import band_profile as band_service

    parsed = _band_day(day)
    async with _service_connection() as conn:
        return _tool_ok(await band_service.preview(conn, None, None, parsed))


@mcp.tool()
@_answers_problems
async def band_profile_preview(
    temp_day: Annotated[float | None, _band_temp("Temperature target at solar noon")] = None,
    temp_night: Annotated[float | None, _band_temp("Temperature target at solar midnight")] = None,
    temp_width_day: Annotated[float | None, _band_temp("± around the day target", 0)] = None,
    temp_width_night: Annotated[float | None, _band_temp("± around the night target", 0)] = None,
    vpd_day: Annotated[float | None, _band_vpd("VPD target at solar noon")] = None,
    vpd_night: Annotated[float | None, _band_vpd("VPD target at solar midnight")] = None,
    vpd_below_day: Annotated[float | None, _band_vpd("Range below the day target", 0)] = None,
    vpd_above_day: Annotated[float | None, _band_vpd("Range above the day target", 0)] = None,
    vpd_below_night: Annotated[float | None, _band_vpd("Range below the night target", 0)] = None,
    vpd_above_night: Annotated[float | None, _band_vpd("Range above the night target", 0)] = None,
    anchors: Annotated[
        dict[str, float] | None,
        Field(description='Advanced: raw house anchors by param, such as {"band_temp_low_sr": 62.5}.'),
    ] = None,
    day: Annotated[str | None, Field(description=_BAND_DAY_DESCRIPTION)] = None,
):
    """Evaluate a draft of the house band without saving it. REST twin: API-23 (GET /v1/band/profile) with the same
    fields; the same JSON (BandProfilePreview).

    It only reads: nothing is stored and nothing reaches the controller. A field left out keeps the band's value; any
    temperature field recompiles the temperature series with sunrise and sunset at the midpoint of noon and midnight,
    any VPD field the VPD series; anchors then override single raw anchors. Returns current and draft (profile, anchors,
    simple), changed (the anchors a Save would write), both curves over the day at 10-min steps of t, vpd.device (the
    draft with the controller's night bias), findings (error: band_order, guardrail, night_floor, the 0.74 kPa night
    dry-out floor; warning: overshoot, dewpoint_unreached), the night heat estimate for both, the expected night VPD,
    measured (the last 24 h of the house average on t) and forecast (outdoor). Unknown values are null, never 0.

    There is no band write tool: the band is operator-owned and is saved on the dashboard (Control → Band). Refusals
    return the Problem JSON with isError: invalid_request (day), unknown_target (an anchor that is not a house band
    anchor), out_of_range (an anchor outside its registry bounds), read_only (the house band has no complete anchor
    set), query_timeout and unavailable (both retryable). A field outside its schema is refused before the tool runs."""
    from verdify_services import band_profile as band_service

    patch = band_service.BandProfilePatch(
        temp_day=temp_day,
        temp_night=temp_night,
        temp_width_day=temp_width_day,
        temp_width_night=temp_width_night,
        vpd_day=vpd_day,
        vpd_night=vpd_night,
        vpd_below_day=vpd_below_day,
        vpd_above_day=vpd_above_day,
        vpd_below_night=vpd_below_night,
        vpd_above_night=vpd_above_night,
    )
    parsed = _band_day(day)
    async with _service_connection() as conn:
        return _tool_ok(await band_service.preview(conn, patch, dict(anchors or {}), parsed))


# ═══════════════════════════════════════════════════════════════
# LESSON MANAGEMENT
# ═══════════════════════════════════════════════════════════════


async def _lesson_state_row(conn, lesson_id: int) -> dict | None:
    """Fetch the columns needed to derive a lesson's lifecycle state.

    `last_validated > created_at` is the authoritative "validated beyond
    creation" signal; `times_validated` is the fallback the derive helper uses
    when that flag is unavailable. Returns None if the lesson doesn't exist.
    """
    return await conn.fetchrow(
        """
        SELECT id, is_active, superseded_by, times_validated,
               (last_validated IS NOT NULL AND created_at IS NOT NULL
                AND last_validated > created_at) AS has_independent_validation
          FROM planner_lessons WHERE id = $1
        """,
        lesson_id,
    )


@mcp.tool()
async def lessons_manage(action: str, lesson_id: int = 0, data: str = "") -> str:
    """Manage planner lessons (accumulated operational knowledge).

    Actions: create, update, deactivate, validate, supersede.
    Lifecycle state machine (G8): proposed -> validated -> superseded/retired.
    State is derived from is_active/superseded_by/validation history; illegal
    transitions (e.g. validating a superseded or retired lesson) are rejected.

    create: data = {"category", "condition", "lesson", "confidence": "low|medium|high"}
    update: data = {"lesson"?, "condition"?, "confidence"?}
    validate: proposed/validated -> validated; increment times_validated, optionally upgrade confidence
    deactivate: proposed/validated -> retired (terminal)
    supersede: proposed/validated -> superseded by a newer lesson;
        data = {"new_id": <existing lesson id>} (terminal)"""
    d = json.loads(data) if data else {}
    conn = await _db()
    try:
        if action == "create":
            try:
                payload = LessonCreate.model_validate(d)
            except ValidationError as e:
                return json.dumps({"error": "LessonCreate validation failed", "details": json.loads(e.json())})
            row = await conn.fetchrow(
                """
                INSERT INTO planner_lessons (category, condition, lesson, confidence, times_validated, is_active, greenhouse_id)
                VALUES ($1, $2, $3, $4, 1, true, 'vallery') RETURNING *""",
                payload.category,
                payload.condition,
                payload.lesson,
                payload.confidence,
            )
            return _json(dict(row))

        elif action == "update" and lesson_id:
            try:
                patch = LessonUpdate.model_validate(d)
            except ValidationError as e:
                return json.dumps({"error": "LessonUpdate validation failed", "details": json.loads(e.json())})
            set_fields = patch.model_dump(exclude_unset=True)
            if not set_fields:
                return json.dumps({"error": "No fields to update"})
            sets = [f"{k} = ${i}" for i, k in enumerate(set_fields, start=2)]
            vals = [lesson_id, *set_fields.values()]
            row = await conn.fetchrow(f"UPDATE planner_lessons SET {', '.join(sets)} WHERE id = $1 RETURNING *", *vals)
            return _json(dict(row)) if row else json.dumps({"error": "Lesson not found"})

        elif action == "deactivate" and lesson_id:
            cur = await _lesson_state_row(conn, lesson_id)
            if cur is None:
                return json.dumps({"error": "Lesson not found"})
            state = derive_lesson_state(
                is_active=cur["is_active"],
                superseded_by=cur["superseded_by"],
                times_validated=cur["times_validated"] or 1,
                has_independent_validation=bool(cur["has_independent_validation"]),
            )
            if not is_legal_lesson_transition(state, "retired"):
                return json.dumps(
                    {"error": f"Illegal transition {state} -> retired", "lesson_id": lesson_id, "state": state}
                )
            if state == "retired":  # idempotent
                return json.dumps({"ok": True, "lesson_id": lesson_id, "action": "deactivated", "state": "retired"})
            await conn.execute("UPDATE planner_lessons SET is_active = false WHERE id = $1", lesson_id)
            return json.dumps({"ok": True, "lesson_id": lesson_id, "action": "deactivated", "state": "retired"})

        elif action == "validate" and lesson_id:
            try:
                val = LessonValidate.model_validate(d) if d else LessonValidate()
            except ValidationError as e:
                return json.dumps({"error": "LessonValidate validation failed", "details": json.loads(e.json())})
            cur = await _lesson_state_row(conn, lesson_id)
            if cur is None:
                return json.dumps({"error": "Lesson not found"})
            state = derive_lesson_state(
                is_active=cur["is_active"],
                superseded_by=cur["superseded_by"],
                times_validated=cur["times_validated"] or 1,
                has_independent_validation=bool(cur["has_independent_validation"]),
            )
            if not is_legal_lesson_transition(state, "validated"):
                return json.dumps(
                    {"error": f"Illegal transition {state} -> validated", "lesson_id": lesson_id, "state": state}
                )
            if val.confidence:
                await conn.execute(
                    "UPDATE planner_lessons SET times_validated = times_validated + 1, "
                    "last_validated = now(), confidence = $2 WHERE id = $1",
                    lesson_id,
                    val.confidence,
                )
            else:
                await conn.execute(
                    "UPDATE planner_lessons SET times_validated = times_validated + 1, last_validated = now() WHERE id = $1",
                    lesson_id,
                )
            return json.dumps({"ok": True, "lesson_id": lesson_id, "action": "validated", "state": "validated"})

        elif action == "supersede" and lesson_id:
            try:
                sup = LessonSupersede.model_validate(d)
            except ValidationError as e:
                return json.dumps({"error": "LessonSupersede validation failed", "details": json.loads(e.json())})
            if sup.new_id == lesson_id:
                return json.dumps({"error": "A lesson cannot supersede itself", "lesson_id": lesson_id})
            cur = await _lesson_state_row(conn, lesson_id)
            if cur is None:
                return json.dumps({"error": "Lesson not found", "lesson_id": lesson_id})
            new_exists = await conn.fetchval("SELECT 1 FROM planner_lessons WHERE id = $1", sup.new_id)
            if new_exists is None:
                return json.dumps({"error": f"Superseding lesson {sup.new_id} not found", "new_id": sup.new_id})
            state = derive_lesson_state(
                is_active=cur["is_active"],
                superseded_by=cur["superseded_by"],
                times_validated=cur["times_validated"] or 1,
                has_independent_validation=bool(cur["has_independent_validation"]),
            )
            if not is_legal_lesson_transition(state, "superseded"):
                return json.dumps(
                    {"error": f"Illegal transition {state} -> superseded", "lesson_id": lesson_id, "state": state}
                )
            # superseded lessons drop out of the live set (is_active=true AND
            # superseded_by IS NULL); also flip is_active=false for clarity.
            await conn.execute(
                "UPDATE planner_lessons SET superseded_by = $2, is_active = false WHERE id = $1",
                lesson_id,
                sup.new_id,
            )
            return json.dumps(
                {
                    "ok": True,
                    "lesson_id": lesson_id,
                    "action": "superseded",
                    "superseded_by": sup.new_id,
                    "state": "superseded",
                }
            )

        return json.dumps({"error": f"Unknown action '{action}'. Use: create, update, deactivate, validate, supersede"})
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# Sprint 23 — Topology + crop-history tools for Iris
# ═══════════════════════════════════════════════════════════════
#
# These expose the topology tables (zones/shelves/positions/equipment)
# and the crop-history views to Iris, so planning decisions can reference
# "what is currently at SOUTH-FLOOR-1" instead of opaque zone strings.


@mcp.tool()
async def topology() -> str:
    """Return the full greenhouse → zones → shelves → positions tree.

    Use this when you need to know the physical layout: what zones exist,
    what shelves each zone contains, and what position slots are
    defined. The planner uses this to validate setpoint scope (e.g.,
    per-zone VPD targets) and the website uses it for navigation.
    """
    conn = await _db()
    try:
        row = await conn.fetchrow(
            "SELECT greenhouse_id, greenhouse_name, zones FROM v_topology_tree WHERE greenhouse_id = 'vallery'"
        )
        if row is None:
            return _json({"error": "topology not available"})
        # asyncpg returns JSONB as str unless a codec is registered
        z = row["zones"]
        zones = json.loads(z) if isinstance(z, str) else z
        return _json(
            {
                "greenhouse_id": row["greenhouse_id"],
                "greenhouse_name": row["greenhouse_name"],
                "zones": zones,
            }
        )
    finally:
        await conn.close()


@mcp.tool()
async def position_current(zone_slug: str = "") -> str:
    """Return the current occupancy of every position (and which crop, if any).

    Args:
        zone_slug: optional — narrow to one zone (south, north, east, west, center).

    Each row: position_label, crop_name, crop_stage, crop_days_in_place, is_occupied.
    Use this to see "what is planted where right now."
    """
    conn = await _db()
    try:
        if zone_slug:
            rows = await conn.fetch(
                "SELECT * FROM v_position_current WHERE greenhouse_id = 'vallery' AND zone_slug = $1",
                zone_slug,
            )
        else:
            rows = await conn.fetch("SELECT * FROM v_position_current WHERE greenhouse_id = 'vallery'")
        return _json([dict(r) for r in rows])
    finally:
        await conn.close()


@mcp.tool()
async def crop_history(position_id: int = 0) -> str:
    """Return the chronological crop history at a given position.

    Args:
        position_id: the integer position_id. Use `position_current()` to find it.

    Returns every crop that has ever been at this position, newest first, with
    planted_date, cleared_at, final_stage, days_in_place, observation_count,
    and harvest_count. Includes both active and historical rows.
    """
    conn = await _db()
    try:
        if not position_id:
            return _json({"error": "position_id required"})
        rows = await conn.fetch(
            """
            SELECT * FROM v_crop_history
            WHERE position_id = $1 AND greenhouse_id = 'vallery'
            ORDER BY planted_date DESC
            """,
            position_id,
        )
        return _json([dict(r) for r in rows])
    finally:
        await conn.close()


@mcp.tool()
async def crop_lifecycle(crop_id: int) -> str:
    """Return a single crop's full lifecycle timeline.

    Args:
        crop_id: integer crop id.

    Returns: planted_date, cleared_at, current_stage, days_alive, event timeline
    (planted/stage_change/transplanted/removed/harvested), harvest totals
    (weight_kg, units, revenue), observation count + avg health score.
    The authoritative per-crop summary for planning decisions and
    retrospective evaluation.
    """
    conn = await _db()
    try:
        row = await conn.fetchrow(
            "SELECT * FROM v_crop_lifecycle WHERE crop_id = $1 AND greenhouse_id = 'vallery'",
            crop_id,
        )
        if row is None:
            return _json({"error": f"crop {crop_id} not found"})
        d = dict(row)
        # Unpack the JSONB events array
        ev = d.get("events")
        if isinstance(ev, str):
            d["events"] = json.loads(ev)
        return _json(d)
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# VECTORIZED RETRIEVAL (Cortex embed.main, migration 296, #1351)
# ═══════════════════════════════════════════════════════════════
#
# lessons_search and knowledge_search embed the query with local Cortex
# embed.main (2560-dim, #1351) and call fn_search_knowledge_embeddings()
# against knowledge_embeddings (migration 296). The provider, endpoint, model
# and key file come from config/ai.yaml models.embedding through
# verdify_schemas.embeddings, which refuses any host but Cortex. A missing key
# or config fails closed with a classified error instead of crashing.


class SemanticDependencyFailure(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


async def _semantic_dependency_lifecycle(tool: str, reason: str | None, started_at: datetime) -> None:
    """One canonical warning per tool; only successful retrieval recovers it.

    Lock and timestamps fence concurrent calls: a success that started before a
    newer failure cannot resolve that newer failure. No query or provider error
    text is retained. Existing MCP role grants cover this ordinary alert duty.
    """
    envelope = None
    if reason is not None:
        envelope = AlertEnvelope.model_validate(
            {
                "alert_type": "planner_tool_dependency_failed",
                "severity": "warning",
                "category": "system",
                "message": f"{tool} semantic dependency failed: {reason}",
                "details": {
                    "tool": tool,
                    "reason": reason,
                    "embedding_model": _embeddings.EMBEDDING_MODEL,
                    "embedding_dimensions": _embeddings.EMBEDDING_DIMENSIONS,
                },
            }
        )
    conn = await _db()
    try:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "mcp.semantic_dependency." + tool)
            if envelope is not None:
                await conn.execute(
                    """WITH updated AS (
                        UPDATE public.alert_log SET severity='warning', message=$2, details=$3::jsonb,
                            updated_at=clock_timestamp()
                        WHERE alert_type='planner_tool_dependency_failed' AND source='mcp'
                          AND disposition IN ('open','acknowledged') AND resolved_at IS NULL
                          AND details->>'tool'=$1 RETURNING id
                    ) INSERT INTO public.alert_log (alert_type,severity,category,message,details,source)
                      SELECT 'planner_tool_dependency_failed','warning','system',$2,$3::jsonb,'mcp'
                      WHERE NOT EXISTS (SELECT 1 FROM updated)""",
                    tool,
                    envelope.message,
                    json.dumps(envelope.details),
                )
            else:
                await conn.execute(
                    """UPDATE public.alert_log SET disposition='resolved',resolved_at=clock_timestamp(),
                        resolved_by='mcp.semantic_retrieval',
                        resolution='auto-resolved: same semantic tool completed embedding and retrieval',
                        updated_at=clock_timestamp()
                        WHERE alert_type='planner_tool_dependency_failed' AND source='mcp'
                          AND disposition IN ('open','acknowledged') AND resolved_at IS NULL
                          AND details->>'tool'=$1 AND updated_at <= $2""",
                    tool,
                    started_at,
                )
    finally:
        await conn.close()


async def _semantic_failure_result(tool: str, reason: str, started_at: datetime) -> str:
    try:
        await _semantic_dependency_lifecycle(tool, reason, started_at)
        recorded = True
    except Exception as exc:
        print(f"[mcp.semantic_alert] unavailable: {type(exc).__name__}", file=sys.stderr)
        recorded = False
    return json.dumps(
        {"error": f"{tool} semantic dependency failed", "reason": reason, "canonical_alert_recorded": recorded}
    )


async def _embed_query(text: str) -> list[float]:
    """Embed a query with Cortex embed.main; raise a classified, credential-free dependency failure."""
    try:
        settings = _embeddings.load_embedding_settings()
        api_key = _embeddings.read_api_key(settings)
        return await _embeddings.embed_query(settings, text, api_key=api_key)
    except _embeddings.EmbeddingError as exc:
        print(f"[mcp.embed_query] failed: {exc.reason}", file=sys.stderr)
        reason = exc.reason if exc.reason in _EMBED_FAILURE_REASONS else "embedding_provider_failed"
        raise SemanticDependencyFailure(reason) from None


_EMBED_FAILURE_REASONS = frozenset(
    {
        "embedding_config_invalid",
        "embedding_credential_missing",
        "embedding_provider_failed",
        "embedding_invalid_response",
    }
)


@mcp.tool()
async def lessons_search(query: str, top_k: int = 10, min_confidence: str = "low") -> str:
    """Semantic search across planner_lessons.

    Use this to pull the lessons most relevant to a *forward-looking* condition
    (e.g. "hot dry day with 1100 W/m² solar peak") rather than relying on the
    static top-10-by-confidence list the prompt context surfaces by default.

    Args:
        query: free-text description of the conditions or topic you care about
        top_k: max results (default 10, cap 25)
        min_confidence: 'low' | 'medium' | 'high' — filter by minimum confidence
            of the underlying planner_lessons row. Most lessons are 'low' so
            the default is permissive.

    Returns: JSON array of {id, category, condition, lesson, confidence,
    times_validated, distance} sorted by ascending cosine distance.
    """
    top_k = max(1, min(int(top_k), 25))
    started_at = datetime.now(ZoneInfo("UTC"))
    try:
        embedding = await _embed_query(query)
    except SemanticDependencyFailure as exc:
        return await _semantic_failure_result("lessons_search", exc.reason, started_at)

    rank_floor = {"low": 1, "medium": 2, "high": 3}.get(min_confidence, 1)
    conn = None
    try:
        conn = await _db()
        rows = await conn.fetch(
            """
            WITH hits AS (
              SELECT source_id, content, metadata, distance
                FROM fn_search_knowledge_embeddings($1::halfvec, $2, ARRAY['lesson']::text[])
            )
            SELECT pl.id, pl.category, pl.condition, pl.lesson, pl.confidence,
                   pl.times_validated, pl.is_active, h.distance
              FROM hits h
              JOIN planner_lessons pl ON pl.id::text = h.source_id
             WHERE pl.is_active = true AND pl.superseded_by IS NULL
               AND NOT fn_dli_proxy_lesson_invalid(pl.condition, pl.lesson)
               AND CASE pl.confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END >= $3
             ORDER BY h.distance
            """,
            _embeddings.halfvec_literal(embedding),
            top_k,
            rank_floor,
        )
        result = _json([dict(r) for r in rows])
    except Exception:
        return await _semantic_failure_result("lessons_search", "semantic_query_failed", started_at)
    finally:
        if conn is not None:
            await conn.close()
    try:
        await _semantic_dependency_lifecycle("lessons_search", None, started_at)
    except Exception as exc:
        print(f"[mcp.semantic_alert] unavailable: {type(exc).__name__}", file=sys.stderr)
        return json.dumps(
            {
                "error": "semantic retrieval succeeded but canonical lifecycle unavailable",
                "canonical_alert_resolved": False,
            }
        )
    return result


@mcp.tool()
async def knowledge_search(
    query: str,
    top_k: int = 8,
    source_types: str = "lesson,plan,site_doc,playbook,observation",
) -> str:
    """Semantic search across docs, playbook, historical plans, lessons, and observations.

    Use this when you need reference-level knowledge: "what does the playbook
    say about vent oscillation?", "summarize the controller mode hierarchy",
    "have I seen a 1100 W/m² solar day before, and what did I try?". The
    source_types argument lets you scope the search:

      site_doc — public website Markdown plus operator-facing docs in docs/**/*.md
      playbook — the planner playbook + skills mirror (chunked by heading)
      plan     — past plan_journal hypotheses + actual_outcome rows
      lesson   — planner_lessons rows (same corpus as lessons_search)
      observation — historical crop observations, health notes, and stress tags

    Args:
        query: free-text query
        top_k: max results (default 8, cap 25)
        source_types: comma-separated subset of the five sources

    Returns: JSON array of {source_type, source_id, content, metadata, distance}.
    """
    top_k = max(1, min(int(top_k), 25))
    types = [s.strip() for s in source_types.split(",") if s.strip()]
    valid = {"lesson", "plan", "site_doc", "playbook", "observation"}
    types = [t for t in types if t in valid]
    if not types:
        return json.dumps(
            {"error": "source_types must include at least one of: lesson, plan, site_doc, playbook, observation"}
        )

    started_at = datetime.now(ZoneInfo("UTC"))
    try:
        embedding = await _embed_query(query)
    except SemanticDependencyFailure as exc:
        return await _semantic_failure_result("knowledge_search", exc.reason, started_at)

    conn = None
    try:
        conn = await _db()
        rows = await conn.fetch(
            """
            SELECT source_type, source_id, chunk_idx, content, metadata, distance
              FROM fn_search_knowledge_embeddings($1::halfvec, $2, $3::text[]) h
             WHERE h.source_type <> 'lesson'
                OR EXISTS (
                     SELECT 1
                       FROM planner_lessons pl
                      WHERE pl.id::text = h.source_id
                        AND pl.is_active = true
                        AND pl.superseded_by IS NULL
                        AND NOT fn_dli_proxy_lesson_invalid(pl.condition, pl.lesson)
                   )
            """,
            _embeddings.halfvec_literal(embedding),
            top_k,
            types,
        )
        result = _json([dict(r) for r in rows])
    except Exception:
        return await _semantic_failure_result("knowledge_search", "semantic_query_failed", started_at)
    finally:
        if conn is not None:
            await conn.close()
    try:
        await _semantic_dependency_lifecycle("knowledge_search", None, started_at)
    except Exception as exc:
        print(f"[mcp.semantic_alert] unavailable: {type(exc).__name__}", file=sys.stderr)
        return json.dumps(
            {
                "error": "semantic retrieval succeeded but canonical lifecycle unavailable",
                "canonical_alert_resolved": False,
            }
        )
    return result


# ═══════════════════════════════════════════════════════════════
# IRRIGATION WRITES (VD-64, #1177; VD-61, #1174)
# ═══════════════════════════════════════════════════════════════
# Thin adapters over verdify_services.irrigation.writes: the same validation, refusal codes and activity row as the
# operator API's API-IRR-03 to -08 and -13, and the same JSON. The actor comes only from current_actor(). None of them
# reads plan_delivery_log or planner_trigger_ledger, so a water write never satisfies or fails a trigger. verdify_services
# and mcp.types are imported inside the functions: CI loads this module under a FastMCP stub without the MCP runtime.

ScheduleLineId = Literal["L01", "L02", "L04", "L05", "L06", "L07", "L08", "L09", "L10"]
MisterLineId = Literal["L04", "L07", "L09"]
LineTarget = Literal[LineId, "all"]

_WaterReason = Annotated[str | None, Field(description="Why, at most 500 characters; shown in Activity.")]
_WaterRequestId = Annotated[
    str | None, Field(description="A UUID; the same one and arguments replay the stored result.")
]
_WaterTriggerId = Annotated[str | None, Field(description="Kept only in the activity log.")]
_WaterPlannerInstance = Annotated[str | None, Field(description="Kept only in the activity log.")]


class ScheduleStart(BaseModel):
    """The OpenAPI ScheduleStart: clock with a local HH:MM, or a solar kind with offset_min (0 when left out)."""

    model_config = ConfigDict(extra="forbid")

    kind: StartKind
    clock: Annotated[
        str | None,
        Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$", description="Local HH:MM, when kind is clock."),
    ] = None
    offset_min: Annotated[
        int | None, Field(ge=-240, le=240, description="Minutes from the solar event, when kind is not clock.")
    ] = None


async def _irrigation_write(tool: str, write):
    """Run one irrigation write as the request's actor (current_actor(), never an argument) on its own
    connection: the service's JSON, or the Problem JSON with isError."""
    from verdify_services.actor import Actor
    from verdify_services.problems import ServiceError, problem

    caller = current_actor()
    if caller is None:
        return _tool_problem(
            problem("forbidden", "A water write needs a resolved caller: Iris, a named agent or admin")
        )
    actor = Actor(source=caller.source, name=caller.name)
    conn = None
    try:
        conn = await _db()
        result = await write(conn, actor)
    except ServiceError as refused:
        return _tool_problem(refused.problem)
    except Exception as exc:  # the database or its connection; never a refusal
        print(f"[mcp.{tool}] failed: {type(exc).__name__}", file=sys.stderr)
        return _tool_problem(
            problem(
                "unavailable",
                "The irrigation service could not answer. Retry with the same request_id: it stores the "
                "write once and returns it if it was stored",
            )
        )
    finally:
        if conn is not None:
            await conn.close()
    return _tool_ok(result.value, replayed=result.replayed)


@mcp.tool()
async def irrigation_schedule_set(
    slot_id: Annotated[
        int | None,
        Field(ge=1, le=65535, description="The schedule to edit or delete; leave it out to create one."),
    ] = None,
    delete: Annotated[bool, Field(description="Delete the schedule slot_id names; send no schedule field.")] = False,
    line_id: Annotated[
        ScheduleLineId | None,
        Field(description="The relay: L01, L02 or L04-L10. To create, give it or circuit_id and variant."),
    ] = None,
    circuit_id: Annotated[
        Literal["wall_drip", "center_drip", "center_mister", "south_mister", "west_mister"] | None,
        Field(
            description="Instead of line_id (#1336): the physical line: wall_drip (every wall and shelf), center_drip, "
            "center_mister, south_mister or west_mister. Send variant with it."
        ),
    ] = None,
    variant: Annotated[
        Literal["clean", "fertilized"] | None,
        Field(
            description="With circuit_id: clean water, or fertilized (opens the fertilizer master valve L03; clean "
            "never does). A variant whose valve is not wired is unknown_target."
        ),
    ] = None,
    days: Annotated[list[Weekday] | None, Field(min_length=1, description="The weekdays. Needed to create.")] = None,
    start: Annotated[ScheduleStart | None, Field(description="When it starts each day. Needed to create.")] = None,
    on_s: Annotated[
        int | None,
        Field(ge=10, le=7200, description="Seconds per run or pulse; misters 10-90. Needed to create."),
    ] = None,
    count: Annotated[int | None, Field(ge=1, le=60, description="Pulses in a mist window; drips 1.")] = None,
    gap_s: Annotated[
        int | None, Field(ge=0, le=600, description="Seconds between pulses: misters 30-600; drips 0.")
    ] = None,
    prewet_s: Annotated[int | None, Field(ge=0, le=1800, description="Fertilizer lines: clean seconds before.")] = None,
    flush_s: Annotated[int | None, Field(ge=0, le=1800, description="Fertilizer lines: clean seconds after.")] = None,
    degraded_only: Annotated[
        bool | None, Field(description="L04, L07 and L09: run only while the zone's VPD sensor is degraded.")
    ] = None,
    enabled: Annotated[bool | None, Field(description="Whether it runs; a disabled schedule stays stored.")] = None,
    label: Annotated[str | None, Field(max_length=60, description="A short name, shown on Water.")] = None,
    manual: Annotated[bool, Field(description="Stored on a create or edit; false unless you pass true.")] = False,
    reason: _WaterReason = None,
    request_id: _WaterRequestId = None,
    trigger_id: _WaterTriggerId = None,
    planner_instance: _WaterPlannerInstance = None,
):
    """Changes watering only; it does not count as your plan for this trigger.

    Create, edit or delete a schedule. REST twins: API-IRR-03 (POST /v1/irrigation/schedules), API-IRR-04 (PUT
    /v1/irrigation/schedules/{slot_id}) and API-IRR-05 (DELETE /v1/irrigation/schedules/{slot_id}). Returns the Schedule
    JSON with sync and manual_before; a delete returns {"slot_id": <id>, "deleted": true, "manual_before": <flag>}.

    No slot_id creates, and needs the line (line_id, or circuit_id and variant), days, start and on_s. An edit may send
    variant alone to switch clean and fertilized on the same physical line. A slot_id edits: omitted fields keep their value, except
    manual. delete true with a slot_id and no schedule field deletes. irrigation_schedule has the bounds per line kind
    and the limit of 24 schedules.

    manual stores false on every create and edit unless you pass true, so an edit clears a person's flag: manual_before
    says so and Activity shows "manual cleared". Only Iris is refused with manual, on an edit or a delete. Before the
    irrigation firmware the write is stored and reaches the controller with the first program push. The same request_id
    (a UUID) and arguments return the stored result with replayed: true.

    Refusals are the Problem JSON with isError: invalid_request, unknown_target, out_of_range (min and max are the bounds
    for the line kind), too_close (two starts on a line under 30 min apart), too_many_schedules, manual,
    request_id_reused, forbidden, and unavailable (the database; retry with the same request_id)."""
    from verdify_services.irrigation import writes
    from verdify_services.problems import problem

    fields = {
        name: value
        for name, value in (
            ("line_id", line_id),
            ("circuit_id", circuit_id),
            ("variant", variant),
            ("days", days),
            ("start", None if start is None else start.model_dump(exclude_none=True)),
            ("on_s", on_s),
            ("count", count),
            ("gap_s", gap_s),
            ("prewet_s", prewet_s),
            ("flush_s", flush_s),
            ("degraded_only", degraded_only),
            ("enabled", enabled),
            ("label", label),
        )
        if value is not None
    }
    audit = {
        "reason": reason,
        "request_id": request_id,
        "trigger_id": trigger_id,
        "planner_instance": planner_instance,
    }

    if delete:
        extra = next(iter(fields), "manual" if manual else None)
        if slot_id is None or extra is not None:
            detail = "delete needs a slot_id" if slot_id is None else f"delete takes no schedule field, not {extra}"
            return _tool_problem(problem("invalid_request", detail, field="slot_id" if slot_id is None else extra))

        async def write(conn, actor):
            return await writes.schedule_delete(conn, actor, slot_id, **audit)

    elif slot_id is None:

        async def write(conn, actor):
            return await writes.schedule_create(conn, actor, fields, manual=manual, **audit)

    else:

        async def write(conn, actor):
            return await writes.schedule_update(conn, actor, slot_id, fields, manual=manual, **audit)

    return await _irrigation_write("irrigation_schedule_set", write)


def _line_write_manual(line_id: str, definition: bool, pause: bool, manual: bool | None) -> bool | None:
    """What a line write passes as manual: REST's line_edit_manual with MCP's default, false (architecture §5.6). A
    definition edit stores the flag it carries. A pause or resume on one line passes none, so the flag stays, even with
    manual beside it. Anything else passes what it was sent: manual alone sets it, and the service refuses it on all."""
    if definition:
        return False if manual is None else manual
    return None if pause and line_id != "all" else manual


@mcp.tool()
async def irrigation_line_set(
    line_id: Annotated[LineTarget, Field(description="The line, L01-L11, or all to pause or resume every line.")],
    enabled: Annotated[
        bool | None, Field(description="Off stops everything on the line, VPD misting included.")
    ] = None,
    vpd_member: Annotated[
        bool | None,
        Field(
            description="L04, L07 and L09: VPD control. When the climate loop wants humidity and its safety gate "
            "passes, the controller may send this mister short mist pulses, one mister at a time by rank (center "
            "first unless another member's own VPD is further above its target), within the line's daily cap and the "
            "daily mist and fog limit. Needs the line enabled (line_disabled otherwise)."
        ),
    ] = None,
    schedule_member: Annotated[bool | None, Field(description="L04, L07 and L09: schedules and runs here.")] = None,
    daily_cap_min: Annotated[int | None, Field(ge=0, le=480, description="Minutes a day; 0 is no cap.")] = None,
    vpd_reserve_min: Annotated[
        int | None, Field(ge=0, le=480, description="Minutes of the cap kept for VPD; at most daily_cap_min.")
    ] = None,
    paused_until: Annotated[
        AwareDatetime | None,
        Field(
            description="ISO date-time with its UTC offset: no scheduled or run-now start until then, though VPD misting "
            "carries on. A time at or before now resumes the line."
        ),
    ] = None,
    manual: Annotated[
        bool | None,
        Field(description="A definition edit stores it, false when left out; alone it sets it; a pause ignores it."),
    ] = None,
    reason: _WaterReason = None,
    request_id: _WaterRequestId = None,
    trigger_id: _WaterTriggerId = None,
    planner_instance: _WaterPlannerInstance = None,
):
    """Changes watering only; it does not count as your plan for this trigger.

    Change one line, or pause or resume every line. REST twin: API-IRR-08 (PUT /v1/irrigation/lines/{line_id}). Returns
    {"lines": [...]}: each changed line as irrigation_status shows it, with manual_before. Only the fields you send
    change; line_id all takes only paused_until and reason.

    manual: a definition edit (enabled, vpd_member, schedule_member, daily_cap_min or vpd_reserve_min) stores what you
    pass, false by default, so it clears a person's flag: manual_before says so and Activity shows "manual cleared". A
    pause or resume never changes it, even with manual beside it; manual alone sets it. Only Iris is refused with manual:
    on a manual line, a pause included, and on all while any line is manual.

    Only a person records a line's one-time check, on the dashboard: enabling a line with no recorded check, or turning
    schedule_member on with none, is check_required, and the same call succeeds once it is recorded. Before the
    irrigation firmware the write is stored and reaches the controller with the first program push. The same request_id
    (a UUID) and arguments return the stored result with replayed: true.

    Refusals are the Problem JSON with isError: out_of_range (min and max are the bounds), check_required,
    invalid_request (all with any field but paused_until, manual included; a mister flag off L04, L07 or L09),
    line_disabled (vpd_member true on a line that stays disabled), unknown_target, manual, request_id_reused, forbidden, and unavailable (the database; retry with the same
    request_id)."""
    from verdify_services.irrigation import writes

    definition = {
        name: value
        for name, value in (
            ("enabled", enabled),
            ("vpd_member", vpd_member),
            ("schedule_member", schedule_member),
            ("daily_cap_min", daily_cap_min),
            ("vpd_reserve_min", vpd_reserve_min),
        )
        if value is not None
    }
    fields = definition if paused_until is None else {**definition, "paused_until": paused_until}
    sent_manual = _line_write_manual(line_id, bool(definition), paused_until is not None, manual)

    async def write(conn, actor):
        return await writes.line_update(
            conn,
            actor,
            line_id,
            fields,
            manual=sent_manual,
            reason=reason,
            request_id=request_id,
            trigger_id=trigger_id,
            planner_instance=planner_instance,
        )

    return await _irrigation_write("irrigation_line_set", write)


@mcp.tool()
async def irrigation_run(
    line_id: Annotated[
        LineId | None,
        Field(
            description="The relay to run: L01, L02, L04-L10, or fog L11; L03 takes no runs. Or give circuit_id and "
            "variant instead. Before the firmware: L01."
        ),
    ] = None,
    circuit_id: Annotated[
        Literal["wall_drip", "center_drip", "center_mister", "south_mister", "west_mister"] | None,
        Field(
            description="Instead of line_id (#1336): the physical line: wall_drip (every wall and shelf), center_drip, "
            "center_mister, south_mister or west_mister. Send variant with it."
        ),
    ] = None,
    variant: Annotated[
        Literal["clean", "fertilized"] | None,
        Field(
            description="With circuit_id: clean water, or fertilized (opens the fertilizer master valve L03; clean "
            "never does). A variant whose valve is not wired is unknown_target."
        ),
    ] = None,
    minutes: Annotated[
        int | None,
        Field(
            ge=1,
            le=120,
            description="Drips L01, L02, L05, L06, and fog L11 (1-30). Before the irrigation firmware: L01, left out "
            "or exactly irrig_wall_duration_min.",
        ),
    ] = None,
    prewet_s: Annotated[
        int | None, Field(ge=0, le=1800, description="L02, L06, L08 and L10: clean seconds before the feed.")
    ] = None,
    flush_s: Annotated[
        int | None, Field(ge=0, le=1800, description="L02, L06, L08 and L10: clean seconds after the feed.")
    ] = None,
    on_s: Annotated[int | None, Field(ge=10, le=90, description="Misters L04, L07-L10: seconds per pulse.")] = None,
    count: Annotated[int | None, Field(ge=1, le=60, description="Misters L04, L07-L10: pulses.")] = None,
    gap_s: Annotated[
        int | None, Field(ge=30, le=600, description="Misters L04, L07-L10: seconds between pulses.")
    ] = None,
    reason: _WaterReason = None,
    request_id: _WaterRequestId = None,
    trigger_id: _WaterTriggerId = None,
    planner_instance: _WaterPlannerInstance = None,
):
    """Changes watering only; it does not count as your plan for this trigger.

    Run one line now, or be refused; nothing is queued. REST twin: API-IRR-06 (POST /v1/irrigation/lines/{line_id}/run).
    Returns the Command JSON: status pending, then sent, or failed (not sent within 2 min, ended by a Stop, or the line
    already running). Whether water started shows on the line's relay in equipment_state within about 30 s, not here.

    Name the line by its relay (line_id) or as a physical line and variant (circuit_id and variant): a fertilized
    variant runs its fertilizer relay with the master valve, a clean one never opens it.

    Send exactly the fields of the line's kind. Fog L11: minutes, 1-30 (unavailable until the controller runs the
    firmware that takes it). Drips L01, L05: minutes. Fertilizer drips L02, L06: minutes, prewet_s,
    flush_s. Misters L04, L07, L09, Schedule on or off: on_s, count, gap_s. Fertilizer misters L08, L10: on_s,
    count, gap_s, prewet_s, flush_s, never minutes; a rinse needs the clean partner enabled, or the controller ends the
    run skipped.

    Before the irrigation firmware, run works on L01 only: the controller's "Wall Drips - RUN CLEAN" press, which runs
    the wall drip for its irrig_wall_duration_min. Leave minutes out, or send exactly that value.
    irrigation_stop always works and ends a run early.

    Run and stop never carry manual. The same request_id (a UUID) and arguments return the stored command with
    replayed: true.

    Refusals are the Problem JSON with isError: line_busy and manifold_busy (retryable, retry_after_s when known),
    cap_reached, line_disabled, line_paused, line_faulted, not_schedule_member (a mister with Schedule off, only until
    the controller runs the firmware that drops that rule), out_of_range (before the firmware, minutes
    is not irrig_wall_duration_min; min and max give it), invalid_request (a missing field or another kind's, such as
    minutes on L08), unknown_target (L03, or a variant whose valve is not wired), request_id_reused, forbidden, and unavailable (a line other than L01
    before the irrigation firmware, not retryable; the database, retryable with the same request_id)."""
    from verdify_services.irrigation import writes

    async def write(conn, actor):
        return await writes.run(
            conn,
            actor,
            line_id,
            minutes=minutes,
            reason=reason,
            request_id=request_id,
            trigger_id=trigger_id,
            planner_instance=planner_instance,
            prewet_s=prewet_s,
            flush_s=flush_s,
            on_s=on_s,
            count=count,
            gap_s=gap_s,
            circuit_id=circuit_id,
            variant=variant,
        )

    return await _irrigation_write("irrigation_run", write)


@mcp.tool()
async def irrigation_stop(
    line_id: Annotated[
        LineId | None, Field(description="The line to stop, L01-L11; leave it out to stop every wet line.")
    ] = None,
    reason: _WaterReason = None,
    request_id: _WaterRequestId = None,
):
    """Changes watering only; it does not count as your plan for this trigger.

    Stop one line, or every wet line when line_id is left out. REST twin: API-IRR-07 (POST /v1/irrigation/stop). Returns
    the Command JSON (kind stop; line_id null for every line). Whether water stopped shows on the relays in
    equipment_state within about 30 s.

    irrigation_stop always works: it is never refused for the firmware, the controller's heap or a writes switch, and it
    goes out ahead of any run. Before the irrigation firmware it ends the wall drip job and switches every open wet relay
    in its scope (L01-L10) off; L11 fog stays with the climate controller.
    After a Stop, VPD misting may pulse again; to keep a line off, disable it. A Stop disables nothing: survival fog and
    the next scheduled run can still start.

    Run and stop never carry manual. The same request_id (a UUID) and arguments return the stored command with
    replayed: true. Refusals are the Problem JSON with isError: unknown_target, invalid_request, request_id_reused,
    forbidden, and unavailable (the database; retry with the same request_id)."""
    from verdify_services.irrigation import writes

    async def write(conn, actor):
        return await writes.stop(conn, actor, line_id, reason=reason, request_id=request_id)

    return await _irrigation_write("irrigation_stop", write)


@mcp.tool()
async def irrigation_clear_fault(
    line_id: Annotated[
        MisterLineId | None, Field(description="The mister to clear: L04, L07 or L09; leave it out for every mister.")
    ] = None,
    reason: _WaterReason = None,
    request_id: _WaterRequestId = None,
):
    """Changes watering only; it does not count as your plan for this trigger.

    Clear a mister's watchdog latch, or every mister's when line_id is left out; harmless with nothing latched. REST
    twin: API-IRR-13 (POST /v1/irrigation/clear-fault). Returns the Command JSON (kind clear_fault; line_id null for every
    mister): pending, then sent, or failed when not sent within 2 min. The line's fault in irrigation_status shows whether
    the latch cleared.

    Clear-fault never carries or checks manual. The same request_id (a UUID) and arguments return the stored command with
    replayed: true. Refusals are the Problem JSON with isError: unknown_target (a line with no latch), request_id_reused,
    forbidden, and unavailable (before the irrigation firmware, not retryable, and nothing is stored; the database,
    retryable with the same request_id)."""
    from verdify_services.irrigation import writes

    async def write(conn, actor):
        return await writes.clear_fault(conn, actor, line_id, reason=reason, request_id=request_id)

    return await _irrigation_write("irrigation_clear_fault", write)


# ═══════════════════════════════════════════════════════════════
# IRRIGATION READS (VD-60, #1173)
# ═══════════════════════════════════════════════════════════════
# Thin adapters over verdify_services.irrigation.reads, the functions the operator API's API-IRR-01, -02, -10 and -11
# call: the same models, so the same JSON, and the same Problem JSON with isError on a refusal.

IrrigationCauseArgument = Literal["vpd", "schedule", "run_now", "feed", "prewet", "flush", "fallback", "unknown"]
RunOutcomeArgument = Literal["ok", "short", "stopped", "preempted", "skipped", "missed", "cap", "unknown"]
RunPurposeArgument = Literal["irrigation", "climate"]
# The REST names of the window arguments: `from` is a Python keyword, so the tool says since and until.
_HISTORY_WINDOW_FIELDS = {"from": "since", "to": "until"}


async def _irrigation_read(tool: str, read):
    """Run one irrigation read on its own connection: the REST JSON, or the Problem JSON with isError. The actor
    comes from the request (current_actor(), never an argument); a read records nothing, so it may be None."""
    from verdify_services.actor import Actor
    from verdify_services.problems import ServiceError, problem

    conn = None
    try:
        caller = current_actor()
        actor = None if caller is None else Actor(source=caller.source, name=caller.name)
        conn = await _db()
        model = await read(conn, actor)
    except ServiceError as refused:
        field = _HISTORY_WINDOW_FIELDS.get(refused.problem.field)
        return _tool_problem(refused.problem.model_copy(update={"field": field}) if field else refused.problem)
    except asyncpg.QueryCanceledError:
        return _tool_problem(
            problem(
                "query_timeout",
                f"The database stopped the query at its {MCP_DB_STATEMENT_TIMEOUT_MS // 1000} s limit. "
                "Narrow the window or retry.",
            )
        )
    except Exception as exc:  # the database or its connection; never a refusal
        print(f"[mcp.{tool}] failed: {type(exc).__name__}", file=sys.stderr)
        return _tool_problem(problem("unavailable", "The irrigation service could not answer. Retry."))
    finally:
        if conn is not None:
            await conn.close()
    return _tool_ok(model)


def _history_local_day(name: str, value: str) -> date:
    from verdify_services.problems import ServiceError, problem

    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ServiceError(
            problem("invalid_request", f"With view minutes, {name} is a local date, YYYY-MM-DD.", field=name)
        ) from exc


def _history_moment(name: str, value: str) -> datetime:
    from verdify_services.problems import ServiceError, problem

    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        moment = None
    if moment is None or moment.utcoffset() is None:
        raise ServiceError(
            problem("invalid_request", f"With view runs, {name} is a date-time with its UTC offset.", field=name)
        )
    return moment


@mcp.tool()
async def irrigation_status():
    """Read the irrigation program as it is now. REST twin: API-IRR-01 (GET /v1/irrigation); the same JSON.

    It only reads and does not count as your plan for a trigger. Returns server_time, the engine flag and the
    program sync, every line (state with its cause and observed_at, flags, manual, pause, fault, today's minutes
    and gallons, next run, last water, last outcome), the active runs, today's minutes by zone, the limits, the leak
    state, occupancy and freshness. Unknown values are null, never 0; a line reads state unknown while the controller
    is not reporting.

    It takes no arguments, so invalid_request and window_too_large never apply. Refusals return the Problem JSON with
    isError: query_timeout (the database stopped the query) and unavailable (the database is out of reach); both are
    retryable."""
    from verdify_services.irrigation import reads

    return await _irrigation_read("irrigation_status", reads.status)


@mcp.tool()
async def get_greenhouse_snapshot():
    """Read the greenhouse snapshot: the 3-5 sentence summary at the top of the dashboard's Now page, written by the
    local Cortex model (llm.primary.longctx) from the current conditions. The same text Now shows (API-02's
    ``snapshot``).

    It only reads and does not count as your plan for a trigger. Returns server_time and snapshot: text,
    generated_at, as_of (the newest run that found the same conditions), model, stale with stale_reason (old: more
    than 30 min; failed: a later attempt failed), and outdated (a band verdict or the open alerts changed since;
    Now hides it). snapshot is null when none was written in the last 2 h. The text is a summary for people, never
    an input to a plan. Refusals: unavailable (the database is out of reach; retryable)."""
    from verdify_services import brief
    from verdify_services.problems import problem

    conn = None
    try:
        conn = await _db()
        model = await brief.read(conn)
    except Exception as exc:  # the database or its connection; never a refusal
        print(f"[mcp.get_greenhouse_snapshot] failed: {type(exc).__name__}", file=sys.stderr)
        return _tool_problem(problem("unavailable", "The greenhouse snapshot could not be read. Retry."))
    finally:
        if conn is not None:
            await conn.close()
    return _tool_ok(model)


@mcp.tool()
async def irrigation_schedule(
    days: Annotated[int, Field(ge=1, le=7, description="Days ahead, today included: 1-7.")] = 7,
    past_days: Annotated[int, Field(ge=0, le=7, description="Days back, planned against actual: 0-7.")] = 0,
):
    """Read the schedules and the week planned against actual. REST twin: API-IRR-02 (GET /v1/irrigation/schedules);
    the same JSON.

    It only reads and does not count as your plan for a trigger. Returns every schedule with its sync, each occurrence
    with its status, the solar times per day, the bounds per line kind (bounds: drip, fert_drip, mister, fert_mister)
    and counts: schedules stored, against max_schedules 24. Before the irrigation firmware a planned occurrence reads
    blocked, "arrives with the irrigation firmware". Unknown values are null, never 0.

    Refusals return the Problem JSON with isError: query_timeout and unavailable (the database; both retryable). days
    or past_days outside their ranges is the REST twin's invalid_request, refused before the tool runs with isError and
    a plain message. Both windows are bounded, so window_too_large never applies."""
    from verdify_services.irrigation import reads

    async def read(conn, actor):
        return await reads.agenda(conn, actor, days=days, past_days=past_days)

    return await _irrigation_read("irrigation_schedule", read)


@mcp.tool()
async def irrigation_history(
    view: Annotated[Literal["minutes", "runs"], Field(description="minutes per day, or the ended runs.")],
    since: Annotated[
        str,
        Field(description="The REST from. minutes: a local date, YYYY-MM-DD. runs: a date-time with its UTC offset."),
    ],
    until: Annotated[
        str,
        Field(
            description="The REST to. minutes: a local date, included. runs: a date-time with its UTC offset, "
            "not included."
        ),
    ],
    group_by: Annotated[
        Literal["zone", "line", "cause"], Field(description="minutes only: what each row totals by.")
    ] = "zone",
    line: Annotated[LineId | None, Field(description="runs only: one line, L01-L11.")] = None,
    cause: Annotated[IrrigationCauseArgument | None, Field(description="runs only: why water ran.")] = None,
    outcome: Annotated[RunOutcomeArgument | None, Field(description="runs only: how a run ended.")] = None,
    cursor: Annotated[str | None, Field(description="runs only: next_cursor of the page before.")] = None,
    purpose: Annotated[
        RunPurposeArgument | None,
        Field(
            description="irrigation (watering: schedule, run_now, feed, prewet, flush) or climate (VPD and fallback "
            "misting, and fog); omit for both."
        ),
    ] = None,
):
    """Read watering history. REST twins: view minutes is API-IRR-10 (GET /v1/irrigation/minutes) and view runs is
    API-IRR-11 (GET /v1/irrigation/runs); the same JSON.

    It only reads and does not count as your plan for a trigger. since and until are the REST from and to.

    View minutes: minutes, pulses and whole gallons per local day (America/Denver), up to 400 days, by group_by and
    always split by cause, purpose and chemistry. A day with coverage no_data has no rows: a gap, never 0.

    View runs: the ended runs, newest first, up to 31 days, 50 to a page, filtered by line, cause, outcome and
    purpose; pass next_cursor as cursor for the next page. The run in progress is in irrigation_status.

    Every row carries its purpose: irrigation (watering plants) or climate (VPD and fallback misting, and every fog
    run), which outnumbers irrigation many times over. purpose keeps one of them in either view; omitted, both are
    returned. "Did the plants get watered?" is purpose irrigation.

    Unknown values are null, never 0. Refusals return the Problem JSON with isError: window_too_large (a wider window;
    max gives the limit), invalid_request (a bad since, until or cursor, or an argument that does not belong to the
    view, such as outcome with minutes; field names it), query_timeout and unavailable (the database; both
    retryable). A value outside an enum is refused before the tool runs, with isError and a plain message."""
    from verdify_services.irrigation import reads
    from verdify_services.problems import ServiceError, problem

    try:
        if view == "minutes":
            other_view = {"line": line, "cause": cause, "outcome": outcome, "cursor": cursor}
            for name, value in other_view.items():
                if value is not None:
                    raise ServiceError(problem("invalid_request", f"{name} belongs to view runs.", field=name))
            first, last = _history_local_day("since", since), _history_local_day("until", until)

            async def read(conn, actor):
                return await reads.minutes(conn, actor, first, last, group_by, purpose=purpose)

        else:
            if group_by != "zone":
                raise ServiceError(problem("invalid_request", "group_by belongs to view minutes.", field="group_by"))
            start, end = _history_moment("since", since), _history_moment("until", until)

            async def read(conn, actor):
                return await reads.runs(
                    conn, actor, start, end, line=line, cause=cause, outcome=outcome, cursor=cursor, purpose=purpose
                )

    except ServiceError as refused:
        return _tool_problem(refused.problem)
    return await _irrigation_read("irrigation_history", read)


# ═══════════════════════════════════════════════════════════════
# PLANTING TOOLS (#1328): verdify_services.crops, the Crops page's service
# ═══════════════════════════════════════════════════════════════
# Records only: no tool here changes a setting, band or target. Open to named agents and admin; Iris stays read-only
# on plantings (its crops tool refuses create and deactivate). Each write returns the planting with its resolved cells
# and id, so undo is remove_planting(end_reason="removed").

_Positions = Annotated[
    list[str],
    Field(
        min_length=1,
        max_length=40,
        description=(
            "Places, each a label (EAST-HYDRO-12), a range (EAST-HYDRO-1..10, hydro 1-30), a rail (rail A top), "
            "shelf cells (west top 1-3), every cell of a place (all south shelves), a shelf (south floor, hanging) "
            "or a zone (center). Ambiguous text is refused with the candidates."
        ),
    ),
]
_PlantingId = Annotated[int, Field(ge=1, le=2**31 - 1, description="The planting id (crops.id).")]
_PlantingWhen = Annotated[
    str | None, Field(description="ISO 8601 time with offset, or a date (local midnight); default now.")
]
_PlantingReason = Annotated[str | None, Field(max_length=500, description="Why; shown in Activity.")]
_PlantingRequestId = Annotated[
    str | None, Field(description="A UUID; the same one and arguments replay the stored result.")
]


def _when(value: str | None):
    """An ISO time or date as the service takes it; text it cannot read is left for its validation."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value) if "T" in value or " " in value.strip() else date.fromisoformat(value)
    except ValueError:
        return value


async def _planting_read(tool: str, read, *, records: str = "The planting records"):
    conn = None
    try:
        conn = await _db()
        model = await read(conn)
    except ServiceError as refused:
        return _tool_problem(refused.problem)
    except asyncpg.QueryCanceledError:
        return _tool_problem(problem("query_timeout", "The database stopped the query at its limit. Retry."))
    except Exception as exc:  # the database or its connection; never a refusal
        print(f"[mcp.{tool}] failed: {type(exc).__name__}", file=sys.stderr)
        return _tool_problem(problem("unavailable", f"{records} could not be read. Retry."))
    finally:
        if conn is not None:
            await conn.close()
    return _tool_ok(model)


async def _planting_write(tool: str, write, *, kind: str = "planting"):
    """One planting (or other records) write as the request's actor (current_actor(), never an argument) on its own
    connection."""
    caller = current_actor()
    if caller is None:
        return _tool_problem(problem("forbidden", f"A {kind} write needs a resolved caller: a named agent or admin"))
    actor = ServiceActor(source=caller.source, name=caller.name)
    conn = None
    try:
        conn = await _db()
        result = await write(conn, actor)
    except ServiceError as refused:
        return _tool_problem(refused.problem)
    except Exception as exc:  # the database or its connection; never a refusal
        print(f"[mcp.{tool}] failed: {type(exc).__name__}", file=sys.stderr)
        return _tool_problem(
            problem(
                "unavailable",
                f"The {kind} service could not answer. Retry with the same request_id: it stores the write once",
            )
        )
    finally:
        if conn is not None:
            await conn.close()
    return _tool_ok(result.value, replayed=result.replayed)


def _body(**fields) -> dict[str, Any]:
    return {name: value for name, value in fields.items() if value is not None}


@mcp.tool()
async def crop_locations(
    zone: Annotated[str | None, Field(description="Keep one zone: center, east, north, south or west.")] = None,
):
    """Read the location tree and what each place holds now. REST twin: API-CROP-01 (GET /v1/crops/locations).

    Returns every zone, its shelves (wall, kind, tier, the irrigation lines that reach it) and their cells (layout
    group, row and column), with the plantings at zone and shelf level and each cell's occupant. A zone or shelf holds
    any number of plantings; a cell at most one. Reads only."""
    return await _planting_read("crop_locations", lambda conn: crop_service.locations(conn, zone=zone))


@mcp.tool()
async def resolve_positions(
    positions: _Positions,
    at: _PlantingWhen = None,
    planting_id: Annotated[
        int | None, Field(ge=1, description="Leave this planting out of the conflicts (its own cells on a move).")
    ] = None,
):
    """Preview what place text means, writing nothing. REST twin: API-CROP-06 (GET /v1/crops/resolve).

    Returns places (zone, shelf, cell), cells, where (compressed, e.g. EAST-HYDRO-1..10) and conflicts: the plantings
    holding those cells at `at`. Refusals: invalid_request (unreadable text), ambiguous_position (current lists the
    candidates)."""
    return await _planting_read(
        "resolve_positions",
        lambda conn: crop_service.resolve(conn, positions, at=_when(at), planting_id=planting_id),
    )


@mcp.tool()
async def record_planting(
    crop: Annotated[str, Field(min_length=1, max_length=200, description="Catalog slug or name (lettuce), or a name.")],
    positions: _Positions,
    variety: Annotated[str | None, Field(max_length=200)] = None,
    count: Annotated[int | None, Field(ge=0, description="Plants in the planting; leave it out when unknown.")] = None,
    count_per_cell: Annotated[int | None, Field(ge=0, description="Plants in each cell, when cells are named.")] = None,
    planted_at: _PlantingWhen = None,
    stage: Annotated[str | None, Field(description="seed (default), germination, seedling, vegetative, ...")] = None,
    notes: Annotated[str | None, Field(max_length=2000)] = None,
    replace: Annotated[
        bool, Field(description="End or trim whatever holds a requested cell instead of refusing.")
    ] = False,
    dry_run: Annotated[bool, Field(description="Resolve and show conflicts only; nothing is written.")] = False,
    reason: _PlantingReason = None,
    request_id: _PlantingRequestId = None,
):
    """Record one planting across the places named. REST twin: API-CROP-07 (POST /v1/crops/plantings).

    Records only: it changes no setting, band or target. Returns the planting with its id, cells and where; undo is
    remove_planting(end_reason="removed"). A cell already held is refused with position_occupied (current names the
    cells) unless replace. dry_run returns the API-CROP-06 preview instead. Refusals: invalid_request,
    ambiguous_position, position_occupied, request_id_reused, forbidden."""
    if dry_run:
        return await _planting_read(
            "record_planting", lambda conn: crop_service.resolve(conn, positions, at=_when(planted_at))
        )
    body = _body(
        crop=crop,
        positions=positions,
        variety=variety,
        count=count,
        count_per_cell=count_per_cell,
        planted_at=_when(planted_at),
        stage=stage,
        notes=notes,
        replace=replace,
        reason=reason,
    )
    return await _planting_write(
        "record_planting", lambda conn, actor: crop_service.plant(conn, actor, body, request_id=request_id)
    )


class PlantingItem(BaseModel):
    """One planting of a bulk re-plant: record_planting's arguments."""

    model_config = ConfigDict(extra="forbid")

    crop: str = Field(min_length=1, max_length=200)
    positions: list[str] = Field(min_length=1, max_length=40)
    variety: str | None = Field(default=None, max_length=200)
    count: int | None = Field(default=None, ge=0)
    count_per_cell: int | None = Field(default=None, ge=0)
    stage: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


@mcp.tool()
async def record_plantings(
    items: Annotated[list[PlantingItem], Field(min_length=1, max_length=40, description="The plantings, in order.")],
    planted_at: _PlantingWhen = None,
    replace: Annotated[bool, Field(description="Each item ends or trims what holds its cells.")] = False,
    reason: _PlantingReason = None,
    request_id: _PlantingRequestId = None,
):
    """Bulk re-plant: record several plantings at one time, each as record_planting would. No REST twin (the
    dashboard sends API-CROP-07 per planting).

    Each item is its own write: one refused does not stop the rest. Returns {"results": [...]}, one per item in
    order, each the planting or {"problem": <Problem JSON>}. With request_id each item's key is derived from it and
    the item's index, so a retry replays what was stored."""
    from uuid import uuid5

    base = None
    if request_id is not None:
        try:
            base = UUID(request_id)
        except ValueError:
            return _tool_problem(problem("invalid_request", "request_id is a UUID", field="request_id"))
    caller = current_actor()
    if caller is None:
        return _tool_problem(problem("forbidden", "A planting write needs a resolved caller: a named agent or admin"))
    actor = ServiceActor(source=caller.source, name=caller.name)
    results: list[dict[str, Any]] = []
    conn = None
    try:
        conn = await _db()
        for index, item in enumerate(items):
            body = _body(**item.model_dump(), planted_at=_when(planted_at), replace=replace, reason=reason)
            key = None if base is None else uuid5(base, f"record_plantings:{index}")
            try:
                result = await crop_service.plant(conn, actor, body, request_id=key)
            except ServiceError as refused:
                results.append({"problem": refused.problem.body()})
                continue
            entry = result.value.model_dump(mode="json")
            results.append({**entry, "replayed": True} if result.replayed else entry)
    except Exception as exc:  # the database or its connection; never a refusal
        print(f"[mcp.record_plantings] failed: {type(exc).__name__}", file=sys.stderr)
        return _tool_problem(
            problem("unavailable", "The planting service could not answer. Retry with the same request_id")
        )
    finally:
        if conn is not None:
            await conn.close()
    return _tool_result({"results": results})


@mcp.tool()
async def move_planting(
    planting_id: _PlantingId,
    to_positions: _Positions,
    from_positions: Annotated[
        list[str] | None, Field(description="The places it leaves; default every place it is now.")
    ] = None,
    moved_at: _PlantingWhen = None,
    replace: Annotated[bool, Field(description="End or trim whatever holds a destination cell.")] = False,
    reason: _PlantingReason = None,
    request_id: _PlantingRequestId = None,
):
    """Move a planting; the old and new places both stay in its history. REST twin: API-CROP-08.

    Refusals: invalid_request, ambiguous_position, position_occupied, planting_ended, unknown_target,
    request_id_reused."""
    body = _body(
        to_positions=to_positions,
        from_positions=from_positions,
        moved_at=_when(moved_at),
        replace=replace,
        reason=reason,
    )
    return await _planting_write(
        "move_planting", lambda conn, actor: crop_service.move(conn, actor, planting_id, body, request_id=request_id)
    )


@mcp.tool()
async def harvest_planting(
    planting_id: _PlantingId,
    count: Annotated[int | None, Field(ge=0, description="Units harvested.")] = None,
    weight_kg: Annotated[float | None, Field(ge=0, le=10000)] = None,
    positions: Annotated[list[str] | None, Field(description="Cells cleared by this harvest; default none.")] = None,
    final: Annotated[bool, Field(description="The last harvest: the planting ends as harvested.")] = False,
    harvested_at: _PlantingWhen = None,
    notes: Annotated[str | None, Field(max_length=2000)] = None,
    reason: _PlantingReason = None,
    request_id: _PlantingRequestId = None,
):
    """Record a harvest; partial unless final. REST twin: API-CROP-09.

    Clearing every place it holds also ends the planting. Refusals: invalid_request, planting_ended, unknown_target,
    request_id_reused."""
    body = _body(
        count=count,
        weight_kg=weight_kg,
        positions=positions,
        final=final,
        harvested_at=_when(harvested_at),
        notes=notes,
        reason=reason,
    )
    return await _planting_write(
        "harvest_planting",
        lambda conn, actor: crop_service.harvest(conn, actor, planting_id, body, request_id=request_id),
    )


@mcp.tool()
async def remove_planting(
    planting_id: _PlantingId,
    end_reason: Annotated[Literal["failed", "removed", "reconciled"], Field(description="Why it ended.")],
    at: _PlantingWhen = None,
    note: Annotated[str | None, Field(max_length=2000)] = None,
    reason: _PlantingReason = None,
    request_id: _PlantingRequestId = None,
):
    """End a planting without a harvest; every open place closes. REST twin: API-CROP-10. Undo by recording it
    again. Refusals: invalid_request, planting_ended, unknown_target, request_id_reused."""
    body = _body(end_reason=end_reason, at=_when(at), note=note, reason=reason)
    return await _planting_write(
        "remove_planting",
        lambda conn, actor: crop_service.end(conn, actor, planting_id, body, request_id=request_id),
    )


@mcp.tool()
async def annotate_planting(
    planting_id: _PlantingId,
    note: Annotated[str, Field(min_length=1, max_length=2000)],
    reason: _PlantingReason = None,
    request_id: _PlantingRequestId = None,
):
    """Add a note to a planting's history (ended plantings too). Returns the event. Refusals: unknown_target,
    request_id_reused."""
    body = _body(note=note, reason=reason)
    return await _planting_write(
        "annotate_planting",
        lambda conn, actor: crop_service.annotate(conn, actor, planting_id, body, request_id=request_id),
    )


@mcp.tool()
async def planting_history(
    planting_id: Annotated[int | None, Field(ge=1, description="One planting: its places, events, harvests.")] = None,
    position: Annotated[str | None, Field(description="One cell label: who has held it, current first.")] = None,
    active: Annotated[
        bool | None, Field(description="With neither: true active (default), false ended, null both.")
    ] = True,
    zone: Annotated[str | None, Field(description="With neither: plantings ever in this zone.")] = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 100,
):
    """Read plantings. REST twins: API-CROP-03 (planting_id), API-CROP-04 (position) and API-CROP-02 (list)."""
    if planting_id is not None:
        return await _planting_read("planting_history", lambda conn: crop_service.planting(conn, planting_id))
    if position is not None:
        return await _planting_read("planting_history", lambda conn: crop_service.position_history(conn, position))
    return await _planting_read(
        "planting_history", lambda conn: crop_service.plantings(conn, active=active, zone=zone, limit=limit)
    )


# Soil-moisture targets (#1345, migration 303): API-SOIL-01/02's twins over verdify_services.soil. wilt_pct is what
# the ingestor's soil_dryout rule pages on; the probe map is read-only here.
_SoilPct = Annotated[float | None, Field(ge=0, le=100, description="Percent volumetric water content.")]


@mcp.tool()
async def soil_targets():
    """Read every probe zone's soil-moisture target. REST twin: API-SOIL-01 (GET /v1/soil-targets).

    Each target has zone, wilt_pct (soil_dryout pages when the probe stays below it for more than 2 h in the day),
    min_pct, max_pct, saturation_pct, notes, the probe it reads (probe, sensor_id, occupancy_zone) and who last
    changed it. Targets are plant-agnostic: no crop is named. Reads only."""
    return await _planting_read("soil_targets", soil_service.targets, records="The soil targets")


@mcp.tool()
async def soil_target_set(
    zone: Annotated[str, Field(min_length=1, max_length=40, description="The probe zone: south_1, south_2 or west.")],
    wilt_pct: _SoilPct = None,
    min_pct: _SoilPct = None,
    max_pct: _SoilPct = None,
    saturation_pct: _SoilPct = None,
    notes: Annotated[str | None, Field(max_length=500)] = None,
    reason: _PlantingReason = None,
    request_id: _PlantingRequestId = None,
):
    """Change one zone's soil-moisture thresholds or note. REST twin: API-SOIL-02 (PUT /v1/soil-targets/{zone}).

    Give only the fields that change; the result must keep 0 <= wilt < min < max < saturation <= 100. A wilt_pct
    change moves the soil_dryout page on the ingestor's next cycle. Returns the stored target. Refusals: not_found
    (unknown zone), out_of_range (order or bounds), invalid_request (no field given)."""
    body = _body(wilt_pct=wilt_pct, min_pct=min_pct, max_pct=max_pct, saturation_pct=saturation_pct, notes=notes)
    return await _planting_write(
        "soil_target_set",
        lambda conn, actor: soil_service.set_target(conn, actor, zone, body, reason=reason, request_id=request_id),
        kind="soil target",
    )


# ═══════════════════════════════════════════════════════════════
# AUTHORIZATION INVENTORY GUARD (#585)
# ═══════════════════════════════════════════════════════════════


def _sync_registered_tool_names() -> frozenset[str] | None:
    """Unfiltered registered tool names, or None under CI import stubs.

    Startup deliberately reads the internal tool manager rather than the
    audience-filtered protocol ``list_tools`` override. Schema-only and logic
    CI import this module with a lightweight FastMCP stub that has no
    _tool_manager; the completeness guard then runs in
    tests/test_mcp_audience_auth.py against the stub's recorded registrations
    instead of here.
    """
    manager = getattr(mcp, "_tool_manager", None)
    if manager is None:
        return None
    return frozenset(tool.name for tool in manager.list_tools())


def _assert_tool_audience_registry_complete() -> None:
    """Fail startup if any registered tool could bypass audience authorization."""
    for name, audiences in TOOL_AUDIENCES.items():
        if not audiences or not audiences <= KNOWN_AUDIENCES:
            raise AssertionError(f"TOOL_AUDIENCES[{name!r}] must be a non-empty subset of {sorted(KNOWN_AUDIENCES)}")
        if "admin" not in audiences:
            raise AssertionError(f"TOOL_AUDIENCES[{name!r}] must include 'admin' (admin means ALL tools)")
    masked = sorted(set(TOOL_AUDIENCES) & set(PENDING_TOOL_AUDIENCES))
    if masked:
        raise AssertionError(f"tools present in both TOOL_AUDIENCES and PENDING_TOOL_AUDIENCES: {masked}")
    registered = _sync_registered_tool_names()
    if registered is None:
        return
    unauthorized = sorted(registered - set(TOOL_AUDIENCES))
    stale = sorted(set(TOOL_AUDIENCES) - registered)
    if unauthorized:
        raise AssertionError(
            f"registered MCP tools missing from TOOL_AUDIENCES (would bypass #585 authorization): {unauthorized}"
        )
    if stale:
        raise AssertionError(f"TOOL_AUDIENCES entries with no registered tool: {stale}")


_assert_tool_audience_registry_complete()


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # One JSON line per allowed dispatch, independent of Uvicorn/root log levels.
    _audit_handler = logging.StreamHandler(sys.stdout)
    _audit_handler.setFormatter(logging.Formatter("%(message)s"))
    _TOOL_AUDIT_LOGGER.addHandler(_audit_handler)
    _TOOL_AUDIT_LOGGER.propagate = False
    os.environ.setdefault("MCP_HTTP_HOST", "127.0.0.1")
    os.environ.setdefault("MCP_HTTP_PORT", "8000")
    if _mcp_runtime_db_role_required():

        async def _startup_db_attestation() -> None:
            conn = await _db()
            await conn.close()

        asyncio.run(_startup_db_attestation())
    mcp.run(transport="streamable-http")
