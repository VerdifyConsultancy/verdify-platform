"""
Verdify Crop Catalog API — FastAPI backend for crop management.

Endpoints: crops CRUD, observations, events, health trends, zones.
Runs on port 8300 (internal network only).

Usage:
    uvicorn main:app --host 0.0.0.0 --port 8300
"""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import re
import smtplib
import sys
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Annotated

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field, ValidationError


def _coerce_jsonb(row_dict: dict, *keys: str) -> dict:
    """asyncpg returns JSONB columns as strings unless a codec is registered.
    Parse the named keys from str → list/dict so response_model validation works.
    """
    for k in keys:
        v = row_dict.get(k)
        if isinstance(v, str):
            row_dict[k] = json.loads(v)
    return row_dict


def _round_half_up(value: float, precision: int) -> float:
    """Match PostgreSQL numeric round() for setpoint values sent to firmware."""
    quantum = Decimal("1").scaleb(-precision)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


# verdify_schemas is mounted at /app/verdify_schemas inside the container
# (see docker-compose.yml api.volumes). Host-side dev runs should prefer the
# current worktree before the deployed /mnt/iris/verdify checkout.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in reversed(("/app", str(_REPO_ROOT), "/mnt/iris/verdify")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from verdify_schemas import (  # noqa: E402
    AlertEnvelope,
    APIStatus,
    CropCreate,
    CropDetail,
    CropHealthSummaryItem,
    CropHistoryEntry,
    CropLifecycle,
    CropListItem,
    CropUpdate,
    EventCreate,
    HealthTrendPoint,
    ObservationCreate,
    ObservationWithCrop,
    PositionCurrentEntry,
    PublicBandTraceLatest,
    PublicBandTraceResponse,
    PublicBandTraceSummary,
    PublicDataHealthCheck,
    PublicDataHealthResponse,
    PublicGpuPowerLatest,
    PublicGpuPowerPoint,
    PublicGpuPowerResponse,
    PublicHomeMetrics,
    PublicInfraCpuLatest,
    PublicInfraCpuPoint,
    PublicPipelineHealthSource,
    PublicPlannerDelivery,
    PublicPlannerHealthResponse,
    PublicPlannerTrigger,
    ZoneDetail,
    ZoneListItem,
)
from verdify_schemas.mcp_responses import ScorecardResponse  # noqa: E402
from verdify_schemas.tunable_registry import (  # noqa: E402
    CROP_BAND_REG,
    LEGACY_SHARED_LIGHTING_REG,
    SETPOINT_MAP_REG,
    registry_value_error,
)

FIRMWARE_SETPOINT_PARAMS = frozenset(SETPOINT_MAP_REG.values())
HOUSE_BAND_COMPUTED_PARAMS = frozenset(
    name for name in CROP_BAND_REG if name.startswith("temp_") or name in {"vpd_high", "vpd_low"}
)
ZONE_VPD_TARGET_PARAMS = frozenset(name for name in CROP_BAND_REG if name.startswith("vpd_target_"))
LEGACY_LIGHTING_COMPUTED_PARAMS = LEGACY_SHARED_LIGHTING_REG & FIRMWARE_SETPOINT_PARAMS
ACTIVITY_MIRROR_PARAMS = frozenset({"activity_start_hour", "activity_start_minute", "activity_duration_min"})
EQUIPMENT_SWITCH_SETPOINTS = {
    "sw_economiser_enabled": "economiser_enabled",
    "sw_fog_closes_vent": "fog_closes_vent",
    "sw_irrigation_enabled": "irrigation_enabled",
    "sw_irrigation_wall_enabled": "irrigation_wall_enabled",
    "sw_irrigation_center_enabled": "irrigation_center_enabled",
    "sw_irrigation_weather_skip": "irrigation_weather_skip",
    "sw_gl_auto_mode": "gl_auto_mode",
}
DIRECT_WET_DEFAULTS = {
    "direct_wet_min_temp_f": 65,
    "direct_wet_wall_start_offset_min": 60,
    "direct_wet_wall_drydown_before_off_min": 120,
    "direct_wet_south_start_offset_min": 60,
    "direct_wet_south_drydown_before_off_min": 120,
    "direct_wet_west_start_offset_min": 60,
    "direct_wet_west_drydown_before_off_min": 120,
    "direct_wet_center_start_offset_min": 120,
    "direct_wet_center_drydown_before_off_min": 180,
    "irrig_wall_days_mask": 127,
    "irrig_wall_fert_days_mask": 127,
    "irrig_center_days_mask": 127,
    "irrig_center_fert_days_mask": 127,
    "sw_direct_wet_gate_enabled": 1,
}


def _activity_policy_values(lighting_row, lighting_circuit_rows) -> dict[str, float | int]:
    """Derive greenhouse biological activity from the main-light runtime policy."""
    main_lighting = next((row for row in lighting_circuit_rows or [] if row["light_key"] == "main"), None)
    if main_lighting:
        activity_start_hour = int(main_lighting["start_hour"])
        activity_duration_min = int(main_lighting["target_light_minutes"])
    elif lighting_row:
        activity_start_hour = int(lighting_row["sunrise_hour"])
        activity_duration_min = int(lighting_row["target_light_hours"]) * 60
    else:
        return {}

    activity_duration_min = max(0, min(1440, activity_duration_min))
    return {
        "activity_start_hour": max(0, min(23, activity_start_hour)),
        "activity_start_minute": 0,
        "activity_duration_min": activity_duration_min,
        "direct_wet_min_temp_f": 65,
        "direct_wet_wall_start_offset_min": 60,
        "direct_wet_wall_drydown_before_off_min": 120,
        "direct_wet_south_start_offset_min": 60,
        "direct_wet_south_drydown_before_off_min": 120,
        "direct_wet_west_start_offset_min": 60,
        "direct_wet_west_drydown_before_off_min": 120,
        "direct_wet_center_start_offset_min": 120,
        "direct_wet_center_drydown_before_off_min": 180,
        "irrig_wall_days_mask": 127,
        "irrig_wall_fert_days_mask": 127,
        "irrig_center_days_mask": 127,
        "irrig_center_fert_days_mask": 127,
        "sw_direct_wet_gate_enabled": 1,
    }


def _align_activity_policy_with_plan(
    activity_values: dict[str, float | int],
    plan_values: dict[str, float],
) -> dict[str, float | int]:
    """Keep pull-fallback activity defaults tied to active main-light overrides."""
    if not activity_values:
        return activity_values
    aligned = dict(activity_values)
    if "gl_main_sunrise_hour" in plan_values:
        aligned["activity_start_hour"] = max(0, min(23, int(plan_values["gl_main_sunrise_hour"])))
    if "gl_main_target_light_minutes" in plan_values:
        aligned["activity_duration_min"] = max(0, min(1440, int(plan_values["gl_main_target_light_minutes"])))
    return aligned


# ── DB Connection ──


def get_db_dsn():
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    host = os.environ.get("DB_HOST", "localhost")
    name = os.environ.get("DB_NAME", "verdify")
    user = os.environ.get("DB_USER", "verdify")
    pw = os.environ.get("DB_PASS", "verdify")
    if host.startswith("/cloudsql/"):
        return f"postgresql://{user}:{pw}@/{name}?host={host}"
    return f"postgresql://{user}:{pw}@{host}:5432/{name}"


pool: asyncpg.Pool = None


async def _init_db_connection(conn: asyncpg.Connection) -> None:
    # Public proof endpoints hit planner/data-health views that are small but
    # complex enough for Postgres JIT to spend seconds compiling. Keep API
    # sessions latency-first; the DB still uses JIT for other clients.
    await conn.execute("SET jit = off")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = await asyncpg.create_pool(
        get_db_dsn(),
        min_size=1,
        max_size=3,
        max_inactive_connection_lifetime=60,
        init=_init_db_connection,
    )
    yield
    await pool.close()


app = FastAPI(
    title="Verdify Crop Catalog API",
    version="1.0.0",
    description="Greenhouse crop management — inventory, observations, health tracking",
    lifespan=lifespan,
    docs_url="/docs" if os.environ.get("VERDIFY_ENABLE_API_DOCS", "").lower() in {"1", "true", "yes"} else None,
    redoc_url="/redoc" if os.environ.get("VERDIFY_ENABLE_API_DOCS", "").lower() in {"1", "true", "yes"} else None,
    # Keep OpenAPI available for contract/drift tests, but noindex it at
    # Traefik/API headers and keep the interactive docs hidden by default.
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://lab.verdify.ai",
        "https://verdify.ai",
        "https://www.verdify.ai",
        "http://localhost:8080",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def noindex_api_responses(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


# ── Models ──
#
# Sprint 21: request-body models moved to /mnt/iris/verdify/verdify_schemas/crops.py.
# Any change to fields or validation rules lives there now; every caller
# (API, MCP crops tool, vault-crop-writer, planner) shares the same shape.

DEFAULT_GREENHOUSE = "vallery"
PLANNER_GATEWAY_LABEL = os.environ.get("VERDIFY_PLANNER_GATEWAY_LABEL", "hermes-iris")
PLANNER_MODEL_LABEL = os.environ.get("VERDIFY_PLANNER_MODEL_LABEL", "hermes-iris/openai:gpt-5.5/high")
WRITE_API_KEY_ENV = "VERDIFY_WRITE_API_KEY"
ALLOW_UNAUTHENTICATED_WRITES_ENV = "VERDIFY_ALLOW_UNAUTHENTICATED_WRITES"
PUBLIC_HOME_METRICS_CACHE_TTL_S = 30.0
_PUBLIC_HOME_METRICS_CACHE: dict[str, tuple[float, PublicHomeMetrics]] = {}
PUBLIC_BAND_TRACE_CACHE_TTL_S = 30.0
_PUBLIC_BAND_TRACE_CACHE: dict[str, tuple[float, PublicBandTraceResponse]] = {}
PUBLIC_GPU_POWER_CACHE_TTL_S = 30.0
_PUBLIC_GPU_POWER_CACHE: dict[str, tuple[float, dict]] = {}
CONTACT_ALLOWED_TOPICS = {"build", "control", "data", "press", "collaboration", "correction", "other"}
CONTACT_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CONTACT_URL_RE = re.compile(r"(https?://|www\.)", re.IGNORECASE)
FORCED_ON_SWITCH_PARAMS = frozenset({"sw_fsm_controller_enabled"})
CONTACT_NOTIFY_SUBJECT_PREFIX = "Verdify contact"
PUBLIC_CAMERA_IDS = {"greenhouse_1", "greenhouse_2"}
FRIGATE_BASE_URL_ENV = "VERDIFY_FRIGATE_PUBLIC_BASE_URL"
GO2RTC_BASE_URL_ENV = "VERDIFY_GO2RTC_PUBLIC_BASE_URL"
CLIMATE_ACTION_PROOF_MISSING_SQL = """
WITH latest AS (
    SELECT *
    FROM climate_action_log
    ORDER BY ts DESC
    LIMIT 1
)
SELECT COALESCE(
    (
        SELECT concat_ws(',',
            CASE WHEN climate_action IS NULL OR climate_action = '' THEN 'climate_action' END,
            CASE WHEN priority_axis IS NULL OR priority_axis = '' THEN 'priority_axis' END,
            CASE WHEN climate_intent_version IS NULL OR climate_intent_version = '' THEN 'climate_intent_version' END,
            CASE WHEN temp_low_f IS NULL THEN 'temp_low_f' END,
            CASE WHEN temp_target_f IS NULL THEN 'temp_target_f' END,
            CASE WHEN temp_high_f IS NULL THEN 'temp_high_f' END,
            CASE WHEN vpd_low_kpa IS NULL THEN 'vpd_low_kpa' END,
            CASE WHEN vpd_target_kpa IS NULL THEN 'vpd_target_kpa' END,
            CASE WHEN vpd_high_kpa IS NULL THEN 'vpd_high_kpa' END,
            CASE WHEN temp_target_delta_f IS NULL THEN 'temp_target_delta_f' END,
            CASE WHEN vpd_target_delta_kpa IS NULL THEN 'vpd_target_delta_kpa' END,
            CASE WHEN temp_band_error_f IS NULL THEN 'temp_band_error_f' END,
            CASE WHEN vpd_band_error_kpa IS NULL THEN 'vpd_band_error_kpa' END,
            CASE
                WHEN relay_truth IS NULL
                  OR jsonb_typeof(relay_truth) <> 'object'
                  OR relay_truth = '{}'::jsonb
                THEN 'relay_truth'
            END,
            CASE
                WHEN sensor_status IS NULL
                  OR jsonb_typeof(sensor_status) <> 'object'
                  OR sensor_status = '{}'::jsonb
                THEN 'sensor_status'
            END,
            CASE
                WHEN sensor_status->>'latest_climate_ts' IS NULL
                  OR sensor_status->>'latest_climate_ts' = ''
                THEN 'sensor_status.latest_climate_ts'
            END,
            CASE
                WHEN CASE
                    WHEN sensor_status->>'latest_climate_age_s' ~ '^[0-9]+$'
                    THEN (sensor_status->>'latest_climate_age_s')::int < 300
                    ELSE false
                END IS NOT true
                THEN 'sensor_status.latest_climate_age_s'
            END,
            CASE
                WHEN sensor_status->>'temp_avg_present' IS DISTINCT FROM 'true'
                THEN 'sensor_status.temp_avg_present'
            END,
            CASE
                WHEN sensor_status->>'vpd_avg_present' IS DISTINCT FROM 'true'
                THEN 'sensor_status.vpd_avg_present'
            END,
            CASE
                WHEN sensor_status->>'band_context_complete' IS DISTINCT FROM 'true'
                THEN 'sensor_status.band_context_complete'
            END
        )
        FROM latest
    ),
    'missing'
)
"""


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _trim(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed[:max_len]


def _clean_email_header(value: str | None, max_len: int = 160) -> str:
    return (_trim((value or "").replace("\r", " ").replace("\n", " "), max_len) or "").strip()


def _client_ip(request: Request) -> str:
    """Prefer Cloudflare's client IP header; store only a salted hash."""
    candidates = [
        request.headers.get("CF-Connecting-IP"),
        (request.headers.get("X-Forwarded-For") or "").split(",")[0],
        request.client.host if request.client else None,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        value = candidate.strip()
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            continue
    return "unknown"


def _contact_ip_hash(ip: str) -> str:
    salt = (
        os.environ.get("VERDIFY_CONTACT_HASH_SALT")
        or os.environ.get(WRITE_API_KEY_ENV)
        or os.environ.get("DB_PASS")
        or "verdify-contact-v1"
    )
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()


def _turnstile_verify_sync(secret: str, token: str, remote_ip: str) -> bool:
    payload = urllib.parse.urlencode(
        {
            "secret": secret,
            "response": token,
            "remoteip": remote_ip,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    return bool(body.get("success"))


def _read_camera_jpeg_sync(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "verdify-public-api/1.0"})
    with urllib.request.urlopen(request, timeout=4) as response:
        data = response.read()
    if len(data) < 1024 or not data.startswith(b"\xff\xd8"):
        raise ValueError("camera snapshot did not look like a JPEG")
    return data


def _fetch_camera_snapshot_sync(camera_id: str, height: int) -> bytes:
    go2rtc_base_url = os.environ.get(GO2RTC_BASE_URL_ENV, "http://192.168.30.142:1984").rstrip("/")
    go2rtc_url = f"{go2rtc_base_url}/api/frame.jpeg?{urllib.parse.urlencode({'src': camera_id, 'h': height})}"
    try:
        return _read_camera_jpeg_sync(go2rtc_url)
    except Exception:
        # Fall back to Frigate's latest detect frame if the source stream is unavailable.
        pass

    base_url = os.environ.get(FRIGATE_BASE_URL_ENV, "http://192.168.30.142:5000").rstrip("/")
    url = f"{base_url}/api/{urllib.parse.quote(camera_id)}/latest.jpg?{urllib.parse.urlencode({'h': height, 'quality': 100})}"
    return _read_camera_jpeg_sync(url)


async def _verify_turnstile_if_configured(token: str | None, remote_ip: str) -> bool:
    secret = os.environ.get("VERDIFY_TURNSTILE_SECRET", "").strip()
    if not secret:
        return False
    if not token:
        raise HTTPException(status_code=400, detail="Missing contact verification token")
    verified = await asyncio.to_thread(_turnstile_verify_sync, secret, token, remote_ip)
    if not verified:
        raise HTTPException(status_code=400, detail="Contact verification failed")
    return True


def _contact_smtp_config() -> dict[str, str | int | bool | None]:
    host = _trim(os.environ.get("VERDIFY_CONTACT_SMTP_HOST"), 255)
    port = _int_env("VERDIFY_CONTACT_SMTP_PORT", 587)
    username = _trim(os.environ.get("VERDIFY_CONTACT_SMTP_USERNAME"), 255)
    password = os.environ.get("VERDIFY_CONTACT_SMTP_PASSWORD")
    use_ssl = _truthy_env("VERDIFY_CONTACT_SMTP_SSL")
    starttls = not use_ssl and os.environ.get("VERDIFY_CONTACT_SMTP_STARTTLS", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    timeout_s = _int_env("VERDIFY_CONTACT_SMTP_TIMEOUT_S", 6)
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "ssl": use_ssl,
        "starttls": starttls,
        "timeout_s": timeout_s,
    }


def _contact_notification_message(
    *,
    submission_id: int,
    created_at,
    notify_to: str,
    name: str,
    email: str,
    topic: str,
    affiliation: str | None,
    message: str,
    user_agent: str | None,
    referrer: str | None,
) -> EmailMessage:
    from_addr = (
        _trim(os.environ.get("VERDIFY_CONTACT_NOTIFY_FROM"), 255)
        or _trim(os.environ.get("VERDIFY_CONTACT_SMTP_USERNAME"), 255)
        or "contact@verdify.ai"
    )
    from_name = _clean_email_header(os.environ.get("VERDIFY_CONTACT_NOTIFY_FROM_NAME") or "Verdify Contact")
    subject_prefix = _clean_email_header(os.environ.get("VERDIFY_CONTACT_NOTIFY_SUBJECT_PREFIX"), 80)
    if not subject_prefix:
        subject_prefix = CONTACT_NOTIFY_SUBJECT_PREFIX

    safe_name = _clean_email_header(name, 120)
    safe_topic = _clean_email_header(topic, 40)
    msg = EmailMessage()
    msg["To"] = notify_to
    msg["From"] = formataddr((from_name, from_addr))
    msg["Reply-To"] = formataddr((safe_name, email))
    msg["Subject"] = f"[{subject_prefix}] {safe_topic}: {safe_name}"
    msg["Message-ID"] = make_msgid(domain="verdify.ai")
    msg.set_content(
        "\n".join(
            [
                f"New Verdify contact submission #{submission_id}",
                "",
                f"Submitted: {created_at}",
                f"Name: {name}",
                f"Reply email: {email}",
                f"Topic: {topic}",
                f"Affiliation: {affiliation or '-'}",
                f"Referrer: {referrer or '-'}",
                f"User agent: {user_agent or '-'}",
                "",
                "Message:",
                message,
                "",
                "Review queue:",
                "docker exec verdify-timescaledb psql -U verdify -d verdify -x -c "
                "\"SELECT * FROM public_contact_submissions WHERE status = 'new' ORDER BY created_at DESC LIMIT 20;\"",
            ]
        )
    )
    return msg


def _send_contact_notification_sync(msg: EmailMessage, smtp_config: dict[str, str | int | bool | None]) -> None:
    host = smtp_config["host"]
    if not host:
        raise RuntimeError("VERDIFY_CONTACT_SMTP_HOST is not configured")

    smtp_cls = smtplib.SMTP_SSL if smtp_config["ssl"] else smtplib.SMTP
    with smtp_cls(str(host), int(smtp_config["port"]), timeout=int(smtp_config["timeout_s"])) as smtp:
        if smtp_config["starttls"]:
            smtp.starttls()
        username = smtp_config["username"]
        password = smtp_config["password"]
        if username and password:
            smtp.login(str(username), str(password))
        smtp.send_message(msg)


async def _notify_contact_submission(
    *,
    submission_id: int,
    created_at,
    notify_to: str | None,
    name: str,
    email: str,
    topic: str,
    affiliation: str | None,
    message: str,
    user_agent: str | None,
    referrer: str | None,
) -> None:
    notify_to = _trim(os.environ.get("VERDIFY_CONTACT_NOTIFY_TO"), 254) or _trim(notify_to, 254)
    smtp_config = _contact_smtp_config()
    if not notify_to or not smtp_config["host"]:
        error = (
            "VERDIFY_CONTACT_SMTP_HOST is not configured" if notify_to else "contact notify recipient is not configured"
        )
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE public_contact_submissions
                SET notification_error = $2
                WHERE id = $1
                """,
                submission_id,
                error,
            )
        return

    msg = _contact_notification_message(
        submission_id=submission_id,
        created_at=created_at,
        notify_to=notify_to,
        name=name,
        email=email,
        topic=topic,
        affiliation=affiliation,
        message=message,
        user_agent=user_agent,
        referrer=referrer,
    )
    try:
        await asyncio.to_thread(_send_contact_notification_sync, msg, smtp_config)
    except Exception as exc:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE public_contact_submissions
                SET notification_status = 'failed',
                    notification_attempted_at = now(),
                    notification_error = $2
                WHERE id = $1
                """,
                submission_id,
                str(exc)[:500],
            )
        return

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public_contact_submissions
            SET notification_status = 'sent',
                notification_attempted_at = now(),
                notification_error = NULL
            WHERE id = $1
            """,
            submission_id,
        )


async def require_write_access(
    request: Request,
    x_verdify_api_key: Annotated[str | None, Header(alias="X-Verdify-API-Key")] = None,
) -> None:
    """Fail closed for mutating routes unless an operator key is configured."""
    if _truthy_env(ALLOW_UNAUTHENTICATED_WRITES_ENV):
        return
    expected = os.environ.get(WRITE_API_KEY_ENV)
    if expected and x_verdify_api_key and hmac.compare_digest(expected, x_verdify_api_key):
        return
    raise HTTPException(
        status_code=403,
        detail=f"Write API disabled for unauthenticated request to {request.url.path}",
    )


def _to_float(value) -> float | None:
    return float(value) if value is not None else None


def _overall_data_health(rows: list[asyncpg.Record]) -> str:
    statuses = {str(r["status"]).lower() for r in rows}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "ok"


class PublicContactSubmission(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    message: str = Field(min_length=20, max_length=4000)
    topic: str = Field(default="other", max_length=40)
    affiliation: str | None = Field(default=None, max_length=160)
    website: str | None = Field(default=None, max_length=200)
    turnstile_token: str | None = Field(default=None, max_length=2048)


async def _parse_contact_submission(request: Request) -> tuple[PublicContactSubmission, bool]:
    content_type = (request.headers.get("Content-Type") or "").lower()
    try:
        if "application/json" in content_type:
            return PublicContactSubmission.model_validate(await request.json()), False

        body = (await request.body()).decode("utf-8")
        form_data = {
            key: values[-1] if values else ""
            for key, values in urllib.parse.parse_qs(body, keep_blank_values=True).items()
        }
        return PublicContactSubmission.model_validate(form_data), True
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="Invalid contact submission") from exc


# ── Setpoints compatibility endpoint ──


@app.get("/setpoints")
@app.get("/api/v1/greenhouses/{greenhouse_id}/setpoints")
async def get_setpoints(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Return current effective setpoints in legacy key=value format.

    Production firmware receives tunables through ESPHome native API pushes and
    cfg_* readbacks. This endpoint is kept aligned for diagnostics, recovery
    tooling, and any future explicitly-enabled pull client.
    """
    # Dispatcher-owned params: temperature uses crop profile envelope directly,
    # VPD uses the DB-owned house control band, and lighting uses the highest
    # active crop DLI to set the photoperiod window firmware enforces.
    async with pool.acquire() as conn:
        # Get latest value per parameter (Tier 1 + band-driven only, no legacy params)
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (parameter) parameter, value
            FROM setpoint_changes WHERE greenhouse_id = $1
              AND parameter = ANY($2::text[])
            ORDER BY parameter, ts DESC
        """,
            greenhouse_id,
            sorted(FIRMWARE_SETPOINT_PARAMS),
        )
        # For band-driven params, compute from crop science + house VPD policy.
        band_row = await conn.fetchrow("SELECT * FROM fn_band_setpoints(now())")
        zone_row = await conn.fetchrow("SELECT * FROM fn_zone_vpd_targets(now())")
        house_row = await conn.fetchrow("SELECT * FROM fn_house_vpd_control_band(now())")
        lighting_row = await conn.fetchrow("SELECT * FROM fn_lighting_policy(now(), $1)", greenhouse_id)
        lighting_circuit_rows = await conn.fetch(
            "SELECT * FROM fn_lighting_minutes_policy(now(), $1) ORDER BY light_key",
            greenhouse_id,
        )
        # Tier 1 #3: fail loud if band computation returned NULL.
        # Without a computed band, recovery tooling would receive partial
        # temp/VPD policy and could mask a dispatcher problem. Better to 503
        # than return a response that looks authoritative but is incomplete.
        if band_row is None or zone_row is None or house_row is None or lighting_row is None:
            existing = await conn.fetchval(
                "SELECT id FROM alert_log WHERE alert_type = 'band_fn_null' AND disposition = 'open' LIMIT 1"
            )
            if existing is None:
                alert = AlertEnvelope.model_validate(
                    {
                        "alert_type": "band_fn_null",
                        "severity": "critical",
                        "category": "system",
                        "message": (
                            "band or lighting policy function returned NULL — compatibility setpoints unavailable"
                        ),
                        "details": {
                            "band_row_null": band_row is None,
                            "zone_row_null": zone_row is None,
                            "house_row_null": house_row is None,
                            "lighting_row_null": lighting_row is None,
                        },
                    }
                )
                await conn.execute(
                    "INSERT INTO alert_log (alert_type, severity, category, message, details, source) "
                    "VALUES ('band_fn_null', 'critical', 'system', $1, $2, 'api')",
                    alert.message,
                    json.dumps(alert.details),
                )
            raise HTTPException(
                status_code=503,
                detail="policy setpoint computation unavailable — check band and lighting policy functions",
            )
        plan_rows = await conn.fetch("SELECT parameter, value FROM v_active_plan")
        params = {r["parameter"]: r["value"] for r in rows}
        plan_values = {r["parameter"]: r["value"] for r in plan_rows}
        plan_params = set(plan_values)
        # Planner overrides for all params. Band params are overwritten below by
        # the same crop/house-band functions used by the dispatcher.
        for r in plan_rows:
            params[r["parameter"]] = r["value"]
        # Band-driven params: use the same source as the live dispatcher so the
        # compatibility endpoint cannot diverge from direct pushes.
        if band_row and house_row:
            for param in HOUSE_BAND_COMPUTED_PARAMS:
                if param.startswith("temp"):
                    band_val = float(band_row[param])
                    precision = 1
                else:
                    key = f"house_{param}"
                    band_val = float(house_row[key])
                    precision = 2
                params[param] = _round_half_up(band_val, precision)
        # Lighting policy params: keep the compatibility endpoint identical to the live
        # dispatcher so stale active plans cannot shorten crop photoperiod.
        if lighting_row:
            lighting_values = {
                "gl_dli_target": _round_half_up(float(lighting_row["target_dli"]), 1),
                "gl_sunrise_hour": int(lighting_row["sunrise_hour"]),
                "gl_sunset_hour": int(lighting_row["cutoff_hour"]),
                "sw_gl_auto_mode": 1,
            }
            main_lighting = next((row for row in lighting_circuit_rows if row["light_key"] == "main"), None)
            if main_lighting:
                lighting_values["gl_lux_threshold"] = _round_half_up(float(main_lighting["lux_on_threshold"]), 0)
                lighting_values["gl_lux_hysteresis"] = _round_half_up(float(main_lighting["lux_hysteresis"]), 0)
            for param in LEGACY_LIGHTING_COMPUTED_PARAMS:
                if param in lighting_values:
                    params[param] = lighting_values[param]
        for row in lighting_circuit_rows:
            key = row["light_key"]
            lighting_values = {
                f"gl_{key}_dli_target": _round_half_up(float(row["legacy_dli_target"]), 1),
                f"gl_{key}_target_light_minutes": int(row["target_light_minutes"]),
                f"gl_{key}_sunrise_hour": int(row["start_hour"]),
                f"gl_{key}_sunset_hour": int(row["cutoff_hour"]),
                f"gl_{key}_lux_threshold": _round_half_up(float(row["lux_on_threshold"]), 0),
                f"gl_{key}_lux_hysteresis": _round_half_up(float(row["lux_hysteresis"]), 0),
                f"gl_{key}_min_on_s": int(row["min_on_s"]),
                f"gl_{key}_min_off_s": int(row["min_off_s"]),
                f"sw_gl_{key}_auto_mode": 1 if row["auto_enabled"] else 0,
            }
            for param, value in lighting_values.items():
                if param not in plan_params:
                    params[param] = value
        activity_values = _align_activity_policy_with_plan(
            _activity_policy_values(lighting_row, lighting_circuit_rows),
            plan_values,
        )
        for param, value in activity_values.items():
            if param not in plan_params or param in ACTIVITY_MIRROR_PARAMS:
                params[param] = value
        # Per-zone VPD targets (from crop data per zone)
        if zone_row:
            for param in ZONE_VPD_TARGET_PARAMS:
                params[param] = _round_half_up(float(zone_row[param]), 2)
        # Mister tuning: band provides defaults, planner can override.
        # Set band-derived values first, then planner values overwrite if present.
        if house_row:
            vpd_hi = float(house_row["house_vpd_high"])
            # Band defaults — will be overwritten by planner values from setpoint_changes if present
            params.setdefault("mister_engage_kpa", _round_half_up(vpd_hi + 0.05, 2))
            params.setdefault("mister_all_kpa", _round_half_up(vpd_hi + 0.25, 2))
        outdoor = await conn.fetchrow(
            """
            SELECT outdoor_temp_f, outdoor_rh_pct FROM climate
            WHERE outdoor_temp_f IS NOT NULL AND greenhouse_id = $1
            ORDER BY ts DESC LIMIT 1
        """,
            greenhouse_id,
        )
        if outdoor:
            if outdoor["outdoor_temp_f"]:
                params["outdoor_temp"] = _round_half_up(outdoor["outdoor_temp_f"], 1)
            if outdoor["outdoor_rh_pct"]:
                params["outdoor_rh"] = _round_half_up(outdoor["outdoor_rh_pct"], 0)
        switch_rows = await conn.fetch(
            """
            WITH switch_map(parameter, equipment) AS (
                SELECT * FROM unnest($1::text[], $2::text[])
            ),
            latest AS (
                SELECT DISTINCT ON (equipment) equipment, state
                  FROM equipment_state
                 WHERE equipment = ANY($2::text[])
                 ORDER BY equipment, ts DESC
            )
            SELECT switch_map.parameter, latest.state
              FROM switch_map
              JOIN latest USING (equipment)
            """,
            list(EQUIPMENT_SWITCH_SETPOINTS.keys()),
            list(EQUIPMENT_SWITCH_SETPOINTS.values()),
        )
        for row in switch_rows:
            params[row["parameter"]] = 1 if row["state"] else 0
    lines = [f"{k}={v}" for k, v in sorted(params.items())]
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(content="\n".join(lines) + "\n")


# ── Lights (ESP32 grow light control via MQTT command) ──


@app.post("/api/v1/greenhouses/{greenhouse_id}/lights/{circuit}/{action}")
async def control_lights(
    greenhouse_id: str,
    circuit: str,
    action: str,
    _write_access: None = Depends(require_write_access),
):
    """Publish light command to MQTT for the Lutron bridge to execute."""
    if circuit not in ("main", "grow") or action not in ("on", "off"):
        raise HTTPException(status_code=400, detail="Invalid circuit or action")
    # For now, record the intent in the DB. The local Lutron bridge
    # or a future MQTT subscriber handles the actual switch.
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO equipment_state (ts, equipment, state, greenhouse_id)
            VALUES (now(), $1, $2, $3)
        """,
            f"grow_light_{circuit}",
            action == "on",
            greenhouse_id,
        )
    return {"light": circuit, "action": action, "greenhouse_id": greenhouse_id, "status": "recorded"}


# ── Root + Health ──


@app.get("/")
async def root():
    return {
        "service": "verdify-api",
        "version": "1.0.0",
        "greenhouse": DEFAULT_GREENHOUSE,
        "docs": "/docs" if app.docs_url else None,
        "status": "/api/v1/status",
        "public_home_metrics": "/api/v1/public/home-metrics",
        "public_band_trace": "/api/v1/public/band-trace",
        "public_data_health": "/api/v1/public/data-health",
        "public_planner_health": "/api/v1/public/planner-health",
        "public_gpu_power": "/api/v1/public/gpu-power",
        "public_contact": "/api/v1/public/contact",
    }


@app.get("/health")
async def health():
    """Health check endpoint for external monitoring (Prometheus, uptime checks)."""
    checks = {}
    overall = "ok"

    async with pool.acquire() as conn:
        # Climate data freshness
        age = await conn.fetchval("SELECT extract(epoch FROM now() - max(ts))::int FROM climate")
        checks["climate_age_seconds"] = age
        if age is None or age > 300:
            overall = "degraded"

        action_age = await conn.fetchval("SELECT extract(epoch FROM now() - max(ts))::int FROM climate_action_log")
        checks["climate_action_log_age_seconds"] = action_age
        if action_age is None or action_age > 300:
            overall = "degraded"

        action_proof_missing = await conn.fetchval(CLIMATE_ACTION_PROOF_MISSING_SQL)
        checks["climate_action_log_proof_missing"] = action_proof_missing or ""
        if action_proof_missing:
            overall = "degraded"

        # Scorecard
        score_row = await conn.fetchrow(
            "SELECT compliance_pct, planner_score FROM v_planner_performance WHERE date = CURRENT_DATE"
        )
        if score_row:
            checks["compliance_pct"] = float(score_row["compliance_pct"]) if score_row["compliance_pct"] else 0
            checks["planner_score"] = float(score_row["planner_score"]) if score_row["planner_score"] else 0

        # Active alerts
        alert_count = await conn.fetchval("SELECT count(*) FROM alert_log WHERE ts > now() - interval '1 hour'")
        checks["active_alerts_1h"] = alert_count

        # Setpoint dispatch freshness
        last_dispatch = await conn.fetchval("SELECT extract(epoch FROM now() - max(ts))::int FROM setpoint_changes")
        checks["last_setpoint_change_seconds"] = last_dispatch

        # ESP32 mode
        mode = await conn.fetchval(
            "SELECT value FROM system_state WHERE entity = 'greenhouse_state' ORDER BY ts DESC LIMIT 1"
        )
        checks["greenhouse_mode"] = mode

    # Service health inferred from data freshness (API runs inside Docker — no systemctl/host access)
    climate_age = checks.get("climate_age_seconds", 999)
    action_age = checks.get("climate_action_log_age_seconds", 999)
    action_proof_missing = checks.get("climate_action_log_proof_missing", "missing")
    checks["service_ingestor"] = "ok" if isinstance(climate_age, (int, float)) and climate_age < 300 else "stale"
    checks["service_climate_action_log"] = (
        "ok" if isinstance(action_age, (int, float)) and action_age < 300 and not action_proof_missing else "stale"
    )
    # MCP server health is monitored by the ingestor (planning_heartbeat), not the API.
    # The API can't reach localhost:8000 from inside Docker (MCP binds to 127.0.0.1 on host).

    return {"status": overall, "checks": checks}


# ── Greenhouse ──


@app.get("/api/v1/greenhouses")
async def list_greenhouses():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM greenhouses ORDER BY name")
    return [dict(r) for r in rows]


@app.get("/api/v1/greenhouses/{greenhouse_id}")
async def get_greenhouse(greenhouse_id: str):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM greenhouses WHERE id = $1", greenhouse_id)
        if not row:
            raise HTTPException(404, f"Greenhouse '{greenhouse_id}' not found")
        crops = await conn.fetchval("SELECT COUNT(*) FROM crops WHERE greenhouse_id = $1 AND is_active", greenhouse_id)
        result = dict(row)
        result["active_crops"] = crops
    return result


# ── Crops (greenhouse-scoped + legacy aliases) ──


@app.get("/api/v1/greenhouses/{greenhouse_id}/crops", response_model=list[CropListItem])
@app.get("/api/v1/crops", response_model=list[CropListItem])  # Legacy alias (defaults to vallery)
async def list_crops(
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    zone: str | None = None,
    stage: str | None = None,
    active: bool = True,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    query = "SELECT c.*, (SELECT ROUND(AVG(o.health_score)::numeric, 2) FROM observations o WHERE o.crop_id = c.id AND o.health_score IS NOT NULL AND o.ts > now() - interval '7 days') AS latest_health FROM crops c WHERE c.is_active = $1 AND c.greenhouse_id = $2"
    params = [active, greenhouse_id]
    idx = 3
    if zone:
        query += f" AND c.zone = ${idx}"
        params.append(zone)
        idx += 1
    if stage:
        query += f" AND c.stage = ${idx}"
        params.append(stage)
        idx += 1
    query += f" ORDER BY c.zone, c.position LIMIT ${idx} OFFSET ${idx + 1}"
    params.extend([limit, offset])

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]


@app.get("/api/v1/crops/{crop_id}", response_model=CropDetail)
async def get_crop(crop_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM crops WHERE id = $1", crop_id)
        if not row:
            raise HTTPException(404, "Crop not found")

        health = await conn.fetchval(
            "SELECT ROUND(AVG(health_score)::numeric, 2) FROM observations WHERE crop_id = $1 AND health_score IS NOT NULL AND ts > now() - interval '7 days'",
            crop_id,
        )

        recent_obs = await conn.fetch(
            "SELECT ts, obs_type, notes, health_score, observer FROM observations WHERE crop_id = $1 ORDER BY ts DESC LIMIT 5",
            crop_id,
        )

        result = dict(row)
        result["latest_health"] = float(health) if health else None
        result["recent_observations"] = [dict(o) for o in recent_obs]
    return result


@app.post("/api/v1/greenhouses/{greenhouse_id}/crops", status_code=201)
@app.post("/api/v1/crops", status_code=201)
async def create_crop(
    crop: CropCreate,
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    _write_access: None = Depends(require_write_access),
):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO crops (name, variety, position, zone, planted_date, expected_harvest,
                stage, count, seed_lot_id, supplier, base_temp_f, target_dli,
                target_vpd_low, target_vpd_high, notes, greenhouse_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            RETURNING *
        """,
            crop.name,
            crop.variety,
            crop.position,
            crop.zone,
            crop.planted_date,
            crop.expected_harvest,
            crop.stage,
            crop.count,
            crop.seed_lot_id,
            crop.supplier,
            crop.base_temp_f,
            crop.target_dli,
            crop.target_vpd_low,
            crop.target_vpd_high,
            crop.notes,
            greenhouse_id,
        )

        # Record the planting event
        await conn.execute(
            "INSERT INTO crop_events (crop_id, event_type, new_stage, source, notes) VALUES ($1, 'planted', $2, 'api', $3)",
            row["id"],
            crop.stage,
            f"Created via API: {crop.name} at {crop.position}",
        )

    return dict(row)


@app.put("/api/v1/crops/{crop_id}")
async def update_crop(crop_id: int, crop: CropUpdate, _write_access: None = Depends(require_write_access)):
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT * FROM crops WHERE id = $1", crop_id)
        if not existing:
            raise HTTPException(404, "Crop not found")

        ALLOWED_COLUMNS = {
            "name",
            "variety",
            "zone",
            "position",
            "stage",
            "planted_date",
            "expected_harvest",
            "notes",
            "is_active",
            "vpd_min",
            "vpd_max",
            "temp_min_f",
            "temp_max_f",
            "dli_target",
        }
        updates = {k: v for k, v in crop.model_dump().items() if v is not None and k in ALLOWED_COLUMNS}
        if not updates:
            return dict(existing)

        set_parts = []
        vals = []
        for i, (k, v) in enumerate(updates.items()):
            set_parts.append(f"{k} = ${i + 1}")
            vals.append(v)
        vals.append(crop_id)
        set_sql = ", ".join(set_parts)

        row = await conn.fetchrow(
            f"UPDATE crops SET {set_sql}, updated_at = now() WHERE id = ${len(vals)} RETURNING *", *vals
        )

        # Record stage change event if stage changed
        if "stage" in updates and updates["stage"] != existing["stage"]:
            await conn.execute(
                "INSERT INTO crop_events (crop_id, event_type, old_stage, new_stage, source) VALUES ($1, 'stage_change', $2, $3, 'api')",
                crop_id,
                existing["stage"],
                updates["stage"],
            )

    return dict(row)


@app.delete("/api/v1/crops/{crop_id}")
async def delete_crop(crop_id: int, _write_access: None = Depends(require_write_access)):
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE crops SET is_active = false, updated_at = now() WHERE id = $1 AND is_active = true", crop_id
        )
        if result == "UPDATE 0":
            raise HTTPException(404, "Crop not found or already inactive")

        await conn.execute(
            "INSERT INTO crop_events (crop_id, event_type, source, notes) VALUES ($1, 'removed', 'api', 'Deactivated via API')",
            crop_id,
        )

    return {"status": "deactivated", "id": crop_id}


# ── Observations ──


@app.get("/api/v1/crops/{crop_id}/observations")
async def list_observations(crop_id: int, limit: int = 20):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM observations WHERE crop_id = $1 ORDER BY ts DESC LIMIT $2", crop_id, limit
        )
    return [dict(r) for r in rows]


@app.post("/api/v1/crops/{crop_id}/observations", status_code=201)
async def create_observation(
    crop_id: int,
    obs: ObservationCreate,
    _write_access: None = Depends(require_write_access),
):
    async with pool.acquire() as conn:
        crop = await conn.fetchrow("SELECT zone, position, zone_id, position_id FROM crops WHERE id = $1", crop_id)
        if not crop:
            raise HTTPException(404, "Crop not found")

        row = await conn.fetchrow(
            """
            INSERT INTO observations (
                crop_id, zone, position, zone_id, position_id, obs_type, notes, severity,
                observer, health_score, species, count, affected_pct, photo_path,
                plant_height_cm, leaf_count, canopy_cover_pct, flowering_count,
                fruit_count, root_condition, mortality_count, stress_tags, source
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12, $13, $14,
                $15, $16, $17, $18, $19, $20, $21, $22, 'api'
            )
            RETURNING *
        """,
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
    return dict(row)


@app.get("/api/v1/observations/recent", response_model=list[ObservationWithCrop])
async def recent_observations(limit: int = 20):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT o.*, c.name AS crop_name, c.zone AS crop_zone
            FROM observations o
            LEFT JOIN crops c ON o.crop_id = c.id
            ORDER BY o.ts DESC LIMIT $1
        """,
            limit,
        )
    return [dict(r) for r in rows]


# ── Events ──


@app.get("/api/v1/crops/{crop_id}/events")
async def list_events(crop_id: int, limit: int = 20):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM crop_events WHERE crop_id = $1 ORDER BY ts DESC LIMIT $2", crop_id, limit
        )
    return [dict(r) for r in rows]


@app.post("/api/v1/crops/{crop_id}/events", status_code=201)
async def create_event(crop_id: int, event: EventCreate, _write_access: None = Depends(require_write_access)):
    async with pool.acquire() as conn:
        crop = await conn.fetchrow("SELECT id FROM crops WHERE id = $1", crop_id)
        if not crop:
            raise HTTPException(404, "Crop not found")

        row = await conn.fetchrow(
            """
            INSERT INTO crop_events (crop_id, event_type, old_stage, new_stage, count, operator, source, notes)
            VALUES ($1, $2, $3, $4, $5, $6, 'api', $7)
            RETURNING *
        """,
            crop_id,
            event.event_type,
            event.old_stage,
            event.new_stage,
            event.count,
            event.operator,
            event.notes,
        )

        # Auto-update crop stage if new_stage provided
        if event.new_stage:
            await conn.execute(
                "UPDATE crops SET stage = $1, updated_at = now() WHERE id = $2", event.new_stage, crop_id
            )

    return dict(row)


# ── Health ──


@app.get("/api/v1/crops/{crop_id}/health", response_model=list[HealthTrendPoint])
async def crop_health(crop_id: int, days: int = 30):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ts, health_score, obs_type, notes, source
            FROM observations
            WHERE crop_id = $1 AND health_score IS NOT NULL AND ts > now() - ($2 || ' days')::interval
            ORDER BY ts
        """,
            crop_id,
            str(days),
        )
    return [dict(r) for r in rows]


@app.get("/api/v1/greenhouses/{greenhouse_id}/health", response_model=list[CropHealthSummaryItem])
@app.get("/api/v1/health/summary", response_model=list[CropHealthSummaryItem])
async def health_summary(greenhouse_id: str = DEFAULT_GREENHOUSE):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.name, c.zone, c.position, c.stage,
                ROUND(AVG(o.health_score)::numeric, 2) AS avg_health,
                COUNT(o.id) AS obs_count,
                MAX(o.ts) AS last_observed
            FROM crops c
            LEFT JOIN observations o ON c.id = o.crop_id AND o.health_score IS NOT NULL AND o.ts > now() - interval '7 days'
            WHERE c.is_active = true AND c.greenhouse_id = $1
            GROUP BY c.id, c.name, c.zone, c.position, c.stage
            ORDER BY c.zone, c.position
        """,
            greenhouse_id,
        )
    return [dict(r) for r in rows]


# ── Zones ──


@app.get("/api/v1/zones", response_model=list[ZoneListItem])
async def list_zones():
    async with pool.acquire() as conn:
        zones = await conn.fetch("""
            SELECT zone,
                COUNT(*) FILTER (WHERE is_active) AS active_crops,
                (SELECT ROUND(temp_avg::numeric, 1) FROM climate
                 WHERE ts > now() - interval '5 minutes' ORDER BY ts DESC LIMIT 1) AS current_temp
            FROM crops GROUP BY zone ORDER BY zone
        """)
    return [dict(z) for z in zones]


@app.get("/api/v1/zones/{zone}", response_model=ZoneDetail)
async def get_zone(zone: str):
    async with pool.acquire() as conn:
        crops = await conn.fetch("SELECT * FROM crops WHERE zone = $1 AND is_active ORDER BY position", zone)
        if not crops:
            raise HTTPException(404, f"No crops in zone '{zone}'")

        observations = await conn.fetch(
            """
            SELECT o.*, c.name AS crop_name
            FROM observations o JOIN crops c ON o.crop_id = c.id
            WHERE c.zone = $1 AND o.ts > now() - interval '7 days'
            ORDER BY o.ts DESC LIMIT 10
        """,
            zone,
        )

    return {
        "zone": zone,
        "crops": [dict(c) for c in crops],
        "recent_observations": [dict(o) for o in observations],
    }


# ── System ──


@app.get("/api/v1/status", response_model=APIStatus)
async def status():
    async with pool.acquire() as conn:
        crop_count = await conn.fetchval(
            """
            SELECT count(DISTINCT crop_catalog_slug)::int
            FROM v_position_current
            WHERE greenhouse_id = $1
              AND is_occupied
              AND crop_catalog_slug IS NOT NULL
            """,
            DEFAULT_GREENHOUSE,
        )
        obs_count = await conn.fetchval("SELECT COUNT(*) FROM observations")
        latest = await conn.fetchval("SELECT MAX(ts) FROM climate")
    return {
        "status": "ok",
        "active_crops": crop_count or 0,
        "observations": obs_count,
        "latest_climate_ts": latest,
    }


@app.get("/api/v1/scorecard", response_model=ScorecardResponse)
async def planner_scorecard(scorecard_date: Annotated[date | None, Query(alias="date")] = None):
    """Planner scorecard metrics for a given date, defaulting to today."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT metric, value FROM fn_planner_scorecard(COALESCE($1::date, CURRENT_DATE)) ORDER BY metric",
            scorecard_date,
        )
    return ScorecardResponse.from_metric_rows(rows)


async def _fetch_public_band_trace_generated_at() -> object:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT now()")


async def _fetch_public_band_trace_latest(greenhouse_id: str) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT *
              FROM fn_band_trace(now() - interval '2 hours', now(), $1)
             ORDER BY ts DESC
             LIMIT 1
            """,
            greenhouse_id,
        )


async def _fetch_public_band_trace_summary(hours: int, greenhouse_id: str) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            WITH rows AS (
                SELECT *
                  FROM fn_band_trace(now() - ($1::int * interval '1 hour'), now(), $2)
            )
            SELECT count(*)::int AS sample_count,
                   round(avg(CASE WHEN crop_temp_in_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS crop_temp_compliance_pct,
                   round(avg(CASE WHEN crop_vpd_in_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS crop_vpd_compliance_pct,
                   round(avg(CASE WHEN crop_both_in_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS crop_both_compliance_pct,
                   round(avg(CASE WHEN fw_temp_in_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS fw_temp_compliance_pct,
                   round(avg(CASE WHEN fw_vpd_in_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS fw_vpd_compliance_pct,
                   round(avg(CASE WHEN fw_both_in_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS fw_both_compliance_pct,
                   round(avg(CASE WHEN readback_matches_fw_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS readback_match_pct,
                   round(avg(CASE WHEN trace_quality_flag = 'ok' THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS ok_trace_pct
              FROM rows
            """,
            hours,
            greenhouse_id,
        )


@app.get("/api/v1/public/band-trace", response_model=PublicBandTraceResponse)
async def public_band_trace(
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
):
    """Public-safe crop-vs-firmware-vs-readback band trace summary."""
    cache_key = f"{greenhouse_id}:{hours}"
    now_mono = time.monotonic()
    cached = _PUBLIC_BAND_TRACE_CACHE.get(cache_key)
    if cached and now_mono - cached[0] < PUBLIC_BAND_TRACE_CACHE_TTL_S:
        return cached[1]

    generated_at, latest, summary = await asyncio.gather(
        _fetch_public_band_trace_generated_at(),
        _fetch_public_band_trace_latest(greenhouse_id),
        _fetch_public_band_trace_summary(hours, greenhouse_id),
    )

    response = PublicBandTraceResponse(
        generated_at=generated_at,
        greenhouse_id=greenhouse_id,
        latest=PublicBandTraceLatest.model_validate(dict(latest)) if latest else None,
        summary=PublicBandTraceSummary(
            hours=hours,
            sample_count=summary["sample_count"] if summary else 0,
            crop_temp_compliance_pct=_to_float(summary["crop_temp_compliance_pct"]) if summary else None,
            crop_vpd_compliance_pct=_to_float(summary["crop_vpd_compliance_pct"]) if summary else None,
            crop_both_compliance_pct=_to_float(summary["crop_both_compliance_pct"]) if summary else None,
            fw_temp_compliance_pct=_to_float(summary["fw_temp_compliance_pct"]) if summary else None,
            fw_vpd_compliance_pct=_to_float(summary["fw_vpd_compliance_pct"]) if summary else None,
            fw_both_compliance_pct=_to_float(summary["fw_both_compliance_pct"]) if summary else None,
            readback_match_pct=_to_float(summary["readback_match_pct"]) if summary else None,
            ok_trace_pct=_to_float(summary["ok_trace_pct"]) if summary else None,
        ),
    )
    _PUBLIC_BAND_TRACE_CACHE[cache_key] = (time.monotonic(), response)
    return response


@app.get("/api/v1/public/data-health", response_model=PublicDataHealthResponse)
async def public_data_health():
    """Public-safe proof freshness and trust-ledger status for launch pages."""
    async with pool.acquire() as conn:
        generated_at = await conn.fetchval("SELECT now()")
        pipeline_rows = await conn.fetch(
            """
            SELECT source, rows_1h, rows_24h, age_s, null_pct_1h
            FROM v_data_pipeline_health
            ORDER BY source
            """
        )
        open_critical_high = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM alert_log
            WHERE disposition = 'open'
              AND severity IN ('critical', 'high')
            """
        )
        climate_action_log_age_s = await conn.fetchval(
            "SELECT extract(epoch FROM now() - max(ts))::int FROM climate_action_log"
        )
        climate_action_proof_missing = await conn.fetchval(CLIMATE_ACTION_PROOF_MISSING_SQL)
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL jit = off")
                await conn.execute("SET LOCAL statement_timeout = '6000ms'")
                trust_rows = await conn.fetch(
                    """
                    SELECT check_name, lower(status) AS status, metric_value, threshold_value, details
                    FROM v_data_trust_ledger
                    ORDER BY
                      CASE lower(status) WHEN 'fail' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
                      check_name
                    """
                )
        except Exception:
            trust_rows = []

    pipeline_by_source = {r["source"]: r for r in pipeline_rows}
    climate_pipeline = pipeline_by_source.get("climate")
    forecast_pipeline = pipeline_by_source.get("forecast")
    climate_age_s = climate_pipeline["age_s"] if climate_pipeline else None
    forecast_age_s = forecast_pipeline["age_s"] if forecast_pipeline else None
    fallback_check_rows = [
        {
            "check_name": "climate_freshness",
            "status": "ok" if climate_age_s is not None and climate_age_s <= 300 else "fail",
            "metric_value": climate_age_s,
            "threshold_value": 300,
            "details": "climate age seconds",
        },
        {
            "check_name": "forecast_freshness",
            "status": "ok" if forecast_age_s is not None and forecast_age_s <= 21600 else "fail",
            "metric_value": forecast_age_s,
            "threshold_value": 21600,
            "details": "weather_forecast fetched_at age seconds",
        },
        {
            "check_name": "climate_action_log_freshness",
            "status": "ok" if climate_action_log_age_s is not None and climate_action_log_age_s <= 300 else "fail",
            "metric_value": climate_action_log_age_s,
            "threshold_value": 300,
            "details": "controller decision/action snapshot age seconds",
        },
        {
            "check_name": "climate_action_log_proof_complete",
            "status": "ok" if not climate_action_proof_missing else "fail",
            "metric_value": 0 if not climate_action_proof_missing else 1,
            "threshold_value": 0,
            "details": (
                "latest controller proof row has graphable target deltas and relay truth"
                if not climate_action_proof_missing
                else f"missing fields: {climate_action_proof_missing}"
            ),
        },
        {
            "check_name": "open_critical_or_high_alerts",
            "status": "ok" if not open_critical_high else "fail",
            "metric_value": open_critical_high or 0,
            "threshold_value": 0,
            "details": "open critical/high alerts",
        },
    ]
    check_rows = [dict(r) for r in trust_rows] if trust_rows else fallback_check_rows
    if not any(r["check_name"] == "open_critical_or_high_alerts" for r in check_rows):
        check_rows.append(fallback_check_rows[-1])
    if not any(r["check_name"] == "climate_action_log_freshness" for r in check_rows):
        check_rows.append(fallback_check_rows[2])
    if not any(r["check_name"] == "climate_action_log_proof_complete" for r in check_rows):
        check_rows.append(fallback_check_rows[3])
    checks = [
        PublicDataHealthCheck(
            name=r["check_name"],
            status=r["status"],
            metric_value=_to_float(r["metric_value"]),
            threshold_value=_to_float(r["threshold_value"]),
            details=r["details"] if isinstance(r["details"], str) else json.dumps(r["details"], sort_keys=True),
        )
        for r in check_rows
    ]
    pipeline_sources = [
        PublicPipelineHealthSource(
            source=r["source"],
            rows_1h=r["rows_1h"],
            rows_24h=r["rows_24h"],
            age_s=r["age_s"],
            null_pct_1h=_to_float(r["null_pct_1h"]),
        )
        for r in pipeline_rows
    ]
    return PublicDataHealthResponse(
        generated_at=generated_at,
        overall_status=_overall_data_health(check_rows),
        checks=checks,
        pipeline_sources=pipeline_sources,
    )


@app.get("/api/v1/public/gpu-power", response_model=PublicGpuPowerResponse)
async def public_gpu_power(
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
    step_minutes: Annotated[int, Query(ge=1, le=60)] = 5,
):
    """Public-safe inference-fleet GPU and CPU telemetry mirrored from exporters."""
    if hours * 60 / step_minutes > 2000:
        raise HTTPException(
            status_code=400, detail="gpu-power request exceeds 2000 time buckets; increase step_minutes"
        )
    cache_key = f"{greenhouse_id}:{hours}:{step_minutes}"
    now_mono = time.monotonic()
    cached = _PUBLIC_GPU_POWER_CACHE.get(cache_key)
    if cached and now_mono - cached[0] < PUBLIC_GPU_POWER_CACHE_TTL_S:
        return cached[1]

    async with pool.acquire() as conn:
        generated_at = await conn.fetchval("SELECT now()")
        latest_rows = await conn.fetch(
            """
            SELECT ts, host, vm_name, purpose, gpu, device, model_name, watts,
                   gpu_util_pct, temperature_c, memory_used_mb, memory_free_mb, age_s
              FROM v_gpu_power_latest
             WHERE greenhouse_id = $1
             ORDER BY host, gpu
            """,
            greenhouse_id,
        )
        series_rows = await conn.fetch(
            """
            SELECT time_bucket($2::int * interval '1 minute', ts) AS bucket_ts,
                   host,
                   max(vm_name) AS vm_name,
                   gpu,
                   avg(watts)::double precision AS watts
              FROM gpu_power
             WHERE ts >= now() - ($1::int * interval '1 hour')
               AND greenhouse_id = $3
             GROUP BY bucket_ts, host, gpu
             ORDER BY bucket_ts, host, gpu
            """,
            hours,
            step_minutes,
            greenhouse_id,
        )
        summary = await conn.fetchrow(
            """
            WITH by_bucket AS (
                SELECT time_bucket($2::int * interval '1 minute', ts) AS bucket_ts,
                       host,
                       gpu,
                       avg(watts)::double precision AS watts
                  FROM gpu_power
                 WHERE ts >= now() - ($1::int * interval '1 hour')
                   AND greenhouse_id = $3
                 GROUP BY bucket_ts, host, gpu
            ),
            totals AS (
                SELECT bucket_ts, sum(watts)::double precision AS total_watts
                  FROM by_bucket
                 GROUP BY bucket_ts
            )
            SELECT max(total_watts)::double precision AS peak_total_watts,
                   avg(total_watts)::double precision AS avg_total_watts
              FROM totals
            """,
            hours,
            step_minutes,
            greenhouse_id,
        )
        cpu_latest_rows = await conn.fetch(
            """
            SELECT ts, host, vm_name, purpose, cpu_util_pct, load1, cores,
                   memory_used_pct, age_s
              FROM v_infra_cpu_latest
             WHERE greenhouse_id = $1
             ORDER BY host
            """,
            greenhouse_id,
        )
        cpu_series_rows = await conn.fetch(
            """
            SELECT time_bucket($2::int * interval '1 minute', ts) AS bucket_ts,
                   host,
                   max(vm_name) AS vm_name,
                   avg(cpu_util_pct)::double precision AS cpu_util_pct,
                   avg(memory_used_pct)::double precision AS memory_used_pct
              FROM infra_cpu
             WHERE ts >= now() - ($1::int * interval '1 hour')
               AND greenhouse_id = $3
             GROUP BY bucket_ts, host
             ORDER BY bucket_ts, host
            """,
            hours,
            step_minutes,
            greenhouse_id,
        )
        cpu_summary = await conn.fetchrow(
            """
            WITH by_bucket AS (
                SELECT time_bucket($2::int * interval '1 minute', ts) AS bucket_ts,
                       avg(cpu_util_pct)::double precision AS avg_cpu_util_pct
                  FROM infra_cpu
                 WHERE ts >= now() - ($1::int * interval '1 hour')
                   AND greenhouse_id = $3
                 GROUP BY bucket_ts
            )
            SELECT max(avg_cpu_util_pct)::double precision AS peak_avg_cpu_util_pct
              FROM by_bucket
            """,
            hours,
            step_minutes,
            greenhouse_id,
        )

    latest = [
        PublicGpuPowerLatest(
            ts=r["ts"],
            host=r["host"],
            vm_name=r["vm_name"],
            purpose=r["purpose"],
            gpu=r["gpu"],
            device=r["device"],
            model_name=r["model_name"],
            watts=round(float(r["watts"]), 1),
            gpu_util_pct=round(float(r["gpu_util_pct"]), 1) if r["gpu_util_pct"] is not None else None,
            temperature_c=round(float(r["temperature_c"]), 1) if r["temperature_c"] is not None else None,
            memory_used_mb=round(float(r["memory_used_mb"]), 1) if r["memory_used_mb"] is not None else None,
            memory_free_mb=round(float(r["memory_free_mb"]), 1) if r["memory_free_mb"] is not None else None,
            age_s=r["age_s"],
        )
        for r in latest_rows
    ]
    series = [
        PublicGpuPowerPoint(
            ts=r["bucket_ts"],
            host=r["host"],
            vm_name=r["vm_name"],
            gpu=r["gpu"],
            watts=round(float(r["watts"]), 1),
        )
        for r in series_rows
    ]
    cpu_latest = [
        PublicInfraCpuLatest(
            ts=r["ts"],
            host=r["host"],
            vm_name=r["vm_name"],
            purpose=r["purpose"],
            cpu_util_pct=round(float(r["cpu_util_pct"]), 1) if r["cpu_util_pct"] is not None else None,
            load1=round(float(r["load1"]), 2) if r["load1"] is not None else None,
            cores=r["cores"],
            memory_used_pct=round(float(r["memory_used_pct"]), 1) if r["memory_used_pct"] is not None else None,
            age_s=r["age_s"],
        )
        for r in cpu_latest_rows
    ]
    cpu_series = [
        PublicInfraCpuPoint(
            ts=r["bucket_ts"],
            host=r["host"],
            vm_name=r["vm_name"],
            cpu_util_pct=round(float(r["cpu_util_pct"]), 1) if r["cpu_util_pct"] is not None else None,
            memory_used_pct=round(float(r["memory_used_pct"]), 1) if r["memory_used_pct"] is not None else None,
        )
        for r in cpu_series_rows
    ]
    latest_total = round(sum(item.watts for item in latest), 1) if latest else None
    latest_gpu_utils = [item.gpu_util_pct for item in latest if item.gpu_util_pct is not None]
    latest_cpu_utils = [item.cpu_util_pct for item in cpu_latest if item.cpu_util_pct is not None]
    payload = PublicGpuPowerResponse(
        generated_at=generated_at,
        greenhouse_id=greenhouse_id,
        source="Verdify mirror of Nexus-scraped DCGM and node-exporter telemetry",
        hours=hours,
        step_minutes=step_minutes,
        latest_total_watts=latest_total,
        latest_gpu_count=len(latest),
        latest_avg_gpu_util_pct=round(sum(latest_gpu_utils) / len(latest_gpu_utils), 1) if latest_gpu_utils else None,
        peak_total_watts=round(float(summary["peak_total_watts"]), 1)
        if summary and summary["peak_total_watts"] is not None
        else None,
        avg_total_watts=round(float(summary["avg_total_watts"]), 1)
        if summary and summary["avg_total_watts"] is not None
        else None,
        latest_avg_cpu_util_pct=round(sum(latest_cpu_utils) / len(latest_cpu_utils), 1) if latest_cpu_utils else None,
        peak_avg_cpu_util_pct=round(float(cpu_summary["peak_avg_cpu_util_pct"]), 1)
        if cpu_summary and cpu_summary["peak_avg_cpu_util_pct"] is not None
        else None,
        latest=latest,
        series=series,
        cpu_latest=cpu_latest,
        cpu_series=cpu_series,
    )
    _PUBLIC_GPU_POWER_CACHE[cache_key] = (time.monotonic(), payload)
    return payload


def _public_planner_trigger(row) -> PublicPlannerTrigger | None:
    if row is None:
        return None
    return PublicPlannerTrigger(
        id=int(row["id"]),
        event_type=row["event_type"],
        event_label=row["event_label"],
        instance=row["instance"],
        expected_at=row["expected_at"],
        due_at=row["due_at"],
        delivered_at=row["delivered_at"],
        resolved_at=row["resolved_at"],
        status=row["status"],
        expected_action=row["expected_action"],
        trigger_id=str(row["trigger_id"]) if row["trigger_id"] else None,
        resulting_plan_id=row["resulting_plan_id"],
    )


def _public_planner_delivery(row) -> PublicPlannerDelivery | None:
    if row is None:
        return None
    hermes_run_id = row["hermes_run_id"]
    return PublicPlannerDelivery(
        id=int(row["id"]),
        event_type=row["event_type"],
        event_label=row["event_label"],
        delivered_at=row["delivered_at"],
        status=row["status"],
        instance=row["instance"],
        session_key=row["session_key"],
        wake_mode=row["wake_mode"],
        gateway_status=row["gateway_status"],
        hermes_run_id=hermes_run_id,
        trigger_id=str(row["trigger_id"]) if row["trigger_id"] else None,
        resulting_plan_id=row["resulting_plan_id"],
        plan_written_at=row["plan_written_at"],
        planner_gateway=PLANNER_GATEWAY_LABEL if hermes_run_id else "legacy",
        planner_model_label=PLANNER_MODEL_LABEL if hermes_run_id else None,
    )


def _pending_sla_age_buckets(row) -> dict[str, int]:
    if row is None:
        return {
            "within_sla": 0,
            "overdue_lt_15m": 0,
            "overdue_15m_1h": 0,
            "overdue_gt_1h": 0,
        }
    return {
        "within_sla": int(row["within_sla"] or 0),
        "overdue_lt_15m": int(row["overdue_lt_15m"] or 0),
        "overdue_15m_1h": int(row["overdue_15m_1h"] or 0),
        "overdue_gt_1h": int(row["overdue_gt_1h"] or 0),
    }


def _active_plan_range_violation_count(rows) -> int:
    violations = 0
    for row in rows:
        parameter = row["parameter"]
        try:
            value = float(row["value"])
        except (TypeError, ValueError):
            violations += 1
            continue
        error = registry_value_error(parameter, value)
        if parameter in FORCED_ON_SWITCH_PARAMS and value < 0.5:
            error = "controller_locked_on"
        if error:
            violations += 1
    return violations


@app.get("/api/v1/public/planner-health", response_model=PublicPlannerHealthResponse)
async def public_planner_health():
    """Public-safe expected-trigger SLA surface for planner reliability."""
    async with pool.acquire() as conn:
        summary = await conn.fetchrow("SELECT * FROM v_planner_trigger_health")
        trigger_projection = """
            SELECT id, event_type, event_label, instance, expected_at, due_at,
                   delivered_at, resolved_at, status, expected_action, trigger_id,
                   resulting_plan_id
              FROM planner_trigger_ledger
        """
        trigger_rows = await conn.fetch(
            trigger_projection
            + """
             WHERE expected_at >= now() - interval '36 hours'
             ORDER BY expected_at DESC
             LIMIT 40
            """
        )
        last_expected = await conn.fetchrow(
            trigger_projection
            + """
             WHERE expected_at >= now() - interval '36 hours'
             ORDER BY expected_at DESC
             LIMIT 1
            """
        )
        last_delivered = await conn.fetchrow(
            trigger_projection
            + """
             WHERE expected_at >= now() - interval '36 hours'
               AND delivered_at IS NOT NULL
             ORDER BY delivered_at DESC
             LIMIT 1
            """
        )
        last_resolved = await conn.fetchrow(
            trigger_projection
            + """
             WHERE expected_at >= now() - interval '36 hours'
               AND resolved_at IS NOT NULL
             ORDER BY resolved_at DESC
             LIMIT 1
            """
        )
        pending_by_sla_age = await conn.fetchrow(
            """
            SELECT
              count(*) FILTER (WHERE due_at >= now())::int AS within_sla,
              count(*) FILTER (
                WHERE due_at < now()
                  AND now() - due_at < interval '15 minutes'
              )::int AS overdue_lt_15m,
              count(*) FILTER (
                WHERE due_at < now()
                  AND now() - due_at >= interval '15 minutes'
                  AND now() - due_at < interval '1 hour'
              )::int AS overdue_15m_1h,
              count(*) FILTER (
                WHERE due_at < now()
                  AND now() - due_at >= interval '1 hour'
              )::int AS overdue_gt_1h
              FROM planner_trigger_ledger
             WHERE expected_at >= now() - interval '36 hours'
               AND status IN ('expected', 'delivered')
            """
        )
        current_delivery = await conn.fetchrow(
            """
            SELECT session_key, hermes_run_id
              FROM plan_delivery_log
             WHERE greenhouse_id = $1
             ORDER BY delivered_at DESC
             LIMIT 1
            """,
            DEFAULT_GREENHOUSE,
        )
        recent_deliveries = await conn.fetch(
            """
            SELECT id, event_type, event_label, delivered_at, status, instance,
                   session_key, wake_mode, gateway_status, hermes_run_id,
                   trigger_id, resulting_plan_id, plan_written_at
              FROM plan_delivery_log
             WHERE greenhouse_id = $1
               AND delivered_at >= now() - interval '36 hours'
             ORDER BY delivered_at DESC
             LIMIT 40
            """,
            DEFAULT_GREENHOUSE,
        )
        active_plan_candidates = await conn.fetch(
            """
            SELECT parameter, value
              FROM setpoint_plan
             WHERE greenhouse_id = $1
               AND is_active = true
               AND source IN ('iris', 'plan')
             ORDER BY ts, parameter
             LIMIT 10000
            """,
            DEFAULT_GREENHOUSE,
        )

    if summary is None:
        raise HTTPException(status_code=503, detail="Planner health view unavailable")

    required_failure_count = int(summary["required_failure_count"] or 0)
    missed_expected_count = int(summary["missed_expected_count"] or 0)
    overdue_delivered_count = int(summary["overdue_delivered_count"] or 0)
    if required_failure_count > 0:
        overall_status = "fail"
    elif missed_expected_count > 0 or overdue_delivered_count > 0:
        overall_status = "warn"
    else:
        overall_status = "ok"

    latest_required = summary["latest_required"] or []
    if isinstance(latest_required, str):
        latest_required = json.loads(latest_required)

    return PublicPlannerHealthResponse(
        generated_at=summary["generated_at"],
        overall_status=overall_status,
        missed_expected_count=missed_expected_count,
        overdue_delivered_count=overdue_delivered_count,
        required_failure_count=required_failure_count,
        recent_expected_count=int(summary["recent_expected_count"] or 0),
        resolved_count=int(summary["resolved_count"] or 0),
        latest_required=latest_required,
        last_expected_trigger=_public_planner_trigger(last_expected),
        last_delivered_trigger=_public_planner_trigger(last_delivered),
        last_resolved_trigger=_public_planner_trigger(last_resolved),
        pending_by_sla_age=_pending_sla_age_buckets(pending_by_sla_age),
        current_session_key=current_delivery["session_key"] if current_delivery else None,
        current_model_label=PLANNER_MODEL_LABEL,
        current_hermes_run_id=current_delivery["hermes_run_id"] if current_delivery else None,
        active_plan_range_violation_count=_active_plan_range_violation_count(active_plan_candidates),
        recent_deliveries=[
            delivery for row in recent_deliveries if (delivery := _public_planner_delivery(row)) is not None
        ],
        recent_triggers=[trigger for r in trigger_rows if (trigger := _public_planner_trigger(r)) is not None],
    )


@app.get("/api/v1/public/cameras/{camera_id}/latest.jpg")
async def public_camera_snapshot(camera_id: str, h: Annotated[int, Query(ge=120, le=1080)] = 1080):
    """Public-safe proxy for the two greenhouse source-stream camera snapshots."""
    if camera_id not in PUBLIC_CAMERA_IDS:
        raise HTTPException(status_code=404, detail="Unknown public camera")
    try:
        data = await asyncio.to_thread(_fetch_camera_snapshot_sync, camera_id, h)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Camera snapshot unavailable") from exc
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=20, stale-while-revalidate=60"},
    )


@app.post("/api/v1/public/contact", status_code=202)
async def public_contact_submission(request: Request):
    """Accept public project contact without publishing a personal email address."""
    payload, is_form_submission = await _parse_contact_submission(request)

    if _trim(payload.website, 200):
        if is_form_submission:
            return RedirectResponse("https://lab.verdify.ai/start/contact?sent=1", status_code=303)
        return {"ok": True, "status": "received"}

    name = _trim(payload.name, 120)
    email = _trim(payload.email, 254)
    message = _trim(payload.message, 4000)
    affiliation = _trim(payload.affiliation, 160)
    topic = (payload.topic or "other").strip().lower()

    if not name or len(name) < 2:
        raise HTTPException(status_code=422, detail="Name must be at least 2 characters")
    if not email or not CONTACT_EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="A valid reply email is required")
    if not message or len(message) < 20:
        raise HTTPException(status_code=422, detail="Message must be at least 20 characters")
    if topic not in CONTACT_ALLOWED_TOPICS:
        topic = "other"
    if len(CONTACT_URL_RE.findall(message)) > 3:
        raise HTTPException(status_code=422, detail="Message contains too many links")

    remote_ip = _client_ip(request)
    ip_hash = _contact_ip_hash(remote_ip)
    turnstile_verified = await _verify_turnstile_if_configured(payload.turnstile_token, remote_ip)
    max_per_ip_hour = _int_env("VERDIFY_CONTACT_MAX_PER_IP_HOUR", 5)
    max_per_email_day = _int_env("VERDIFY_CONTACT_MAX_PER_EMAIL_DAY", 4)

    user_agent = _trim(request.headers.get("User-Agent"), 500)
    referrer = _trim(request.headers.get("Referer"), 500)
    metadata = {
        "source": "lab.verdify.ai/start/contact",
        "cf_ray": _trim(request.headers.get("CF-Ray"), 120),
        "turnstile_configured": bool(os.environ.get("VERDIFY_TURNSTILE_SECRET", "").strip()),
    }

    async with pool.acquire() as conn:
        recent_ip = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM public_contact_submissions
            WHERE ip_hash = $1
              AND created_at > now() - interval '1 hour'
              AND status <> 'spam'
            """,
            ip_hash,
        )
        if recent_ip is not None and recent_ip >= max_per_ip_hour:
            raise HTTPException(status_code=429, detail="Too many contact submissions from this network")

        recent_email = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM public_contact_submissions
            WHERE lower(email) = lower($1)
              AND created_at > now() - interval '1 day'
              AND status <> 'spam'
            """,
            email,
        )
        if recent_email is not None and recent_email >= max_per_email_day:
            raise HTTPException(status_code=429, detail="Too many contact submissions from this address")

        submission = await conn.fetchrow(
            """
            INSERT INTO public_contact_submissions (
              name, email, topic, affiliation, message, ip_hash,
              user_agent, referrer, turnstile_verified, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            RETURNING id, created_at
            """,
            name,
            email,
            topic,
            affiliation,
            message,
            ip_hash,
            user_agent,
            referrer,
            turnstile_verified,
            json.dumps(metadata),
        )
        notify_to = await conn.fetchval(
            "SELECT owner_email FROM greenhouses WHERE id = $1",
            DEFAULT_GREENHOUSE,
        )

    await _notify_contact_submission(
        submission_id=submission["id"],
        created_at=submission["created_at"],
        notify_to=notify_to,
        name=name,
        email=email,
        topic=topic,
        affiliation=affiliation,
        message=message,
        user_agent=user_agent,
        referrer=referrer,
    )

    if is_form_submission:
        return RedirectResponse("https://lab.verdify.ai/start/contact?sent=1", status_code=303)
    return {"ok": True, "status": "received"}


@app.post("/api/v1/admin/contact-notifications/retry")
async def retry_contact_notifications(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    _write_access: None = Depends(require_write_access),
):
    """Retry email notifications for queued contact submissions."""
    async with pool.acquire() as conn:
        notify_to = await conn.fetchval(
            "SELECT owner_email FROM greenhouses WHERE id = $1",
            DEFAULT_GREENHOUSE,
        )
        rows = await conn.fetch(
            """
            SELECT id, created_at, name, email, topic, affiliation, message, user_agent, referrer
            FROM public_contact_submissions
            WHERE notification_status IN ('pending', 'failed')
              AND status <> 'spam'
            ORDER BY created_at
            LIMIT $1
            """,
            limit,
        )

    results = []
    for row in rows:
        await _notify_contact_submission(
            submission_id=row["id"],
            created_at=row["created_at"],
            notify_to=notify_to,
            name=row["name"],
            email=row["email"],
            topic=row["topic"],
            affiliation=row["affiliation"],
            message=row["message"],
            user_agent=row["user_agent"],
            referrer=row["referrer"],
        )
        async with pool.acquire() as conn:
            updated = await conn.fetchrow(
                """
                SELECT id, notification_status, notification_error
                FROM public_contact_submissions
                WHERE id = $1
                """,
                row["id"],
            )
        results.append(dict(updated))

    return {"ok": True, "attempted": len(results), "results": results}


@app.get("/api/v1/public/home-metrics", response_model=PublicHomeMetrics)
async def public_home_metrics(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Launch-safe live metrics for lab.verdify.ai proof cards."""
    cache_key = greenhouse_id
    now_mono = time.monotonic()
    cached = _PUBLIC_HOME_METRICS_CACHE.get(cache_key)
    if cached and now_mono - cached[0] < PUBLIC_HOME_METRICS_CACHE_TTL_S:
        return cached[1]

    async with pool.acquire() as conn:
        generated_at = await conn.fetchval("SELECT now()")
        climate_summary = await conn.fetchrow(
            """
            SELECT count(*)::int AS climate_rows,
                   COALESCE(
                     round((extract(epoch FROM max(ts) - min(ts)) / 86400.0)::numeric, 1),
                     0
                   )::float AS climate_days
            FROM climate
            WHERE greenhouse_id = $1
            """,
            greenhouse_id,
        )
        latest_climate = await conn.fetchrow(
            """
            SELECT ts,
                   extract(epoch FROM now() - ts)::int AS age_s,
                   round(temp_avg::numeric, 1)::float AS indoor_temp_f,
                   round(vpd_avg::numeric, 2)::float AS indoor_vpd_kpa,
                   round(outdoor_temp_f::numeric, 1)::float AS outdoor_temp_f,
                   round(outdoor_rh_pct::numeric, 1)::float AS outdoor_rh_pct
            FROM climate
            WHERE greenhouse_id = $1
            ORDER BY ts DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        active_crops = await conn.fetchval(
            """
            SELECT count(DISTINCT crop_catalog_slug)::int
            FROM v_position_current
            WHERE greenhouse_id = $1
              AND is_occupied
              AND crop_catalog_slug IS NOT NULL
            """,
            greenhouse_id,
        )
        plan_count = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM plan_journal
            WHERE greenhouse_id = $1
              AND plan_id LIKE 'iris-%'
              AND plan_id NOT LIKE 'iris-reactive%'
              AND plan_id NOT LIKE 'iris-fix%'
            """,
            greenhouse_id,
        )
        lesson_count = await conn.fetchval(
            "SELECT count(*)::int FROM planner_lessons WHERE greenhouse_id = $1 AND is_active",
            greenhouse_id,
        )
        last_plan = await conn.fetchrow(
            """
            SELECT plan_id, created_at, extract(epoch FROM now() - created_at)::int AS age_s
            FROM plan_journal
            WHERE greenhouse_id = $1
              AND plan_id LIKE 'iris-%'
              AND plan_id NOT LIKE 'iris-reactive%'
              AND plan_id NOT LIKE 'iris-fix%'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        score_rows = await conn.fetch(
            "SELECT metric, value FROM fn_planner_scorecard((now() AT TIME ZONE 'America/Denver')::date)"
        )
        scorecard = {r["metric"]: _to_float(r["value"]) for r in score_rows}
        open_critical_high = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM alert_log
            WHERE greenhouse_id = $1
              AND disposition = 'open'
              AND severity IN ('critical', 'high')
            """,
            greenhouse_id,
        )
        forecast_health = await conn.fetchrow(
            """
            SELECT age_s
            FROM v_data_pipeline_health
            WHERE source = 'forecast'
            """
        )
        latest_action = await conn.fetchrow(
            """
            SELECT extract(epoch FROM now() - ts)::int AS age_s,
                   climate_action,
                   priority_axis,
                   round(temp_target_delta_f::numeric, 2)::float AS temp_target_delta_f,
                   round(vpd_target_delta_kpa::numeric, 3)::float AS vpd_target_delta_kpa,
                   round(temp_band_error_f::numeric, 2)::float AS temp_band_error_f,
                   round(vpd_band_error_kpa::numeric, 3)::float AS vpd_band_error_kpa,
                   moisture_assist_state,
                   wet_assist_allowed,
                   wet_assist_block_reason,
                   fog_allowed,
                   fog_block_reason,
                   relay_truth,
                   sensor_status
            FROM climate_action_log
            WHERE COALESCE(greenhouse_id, 'vallery') = $1
            ORDER BY ts DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        latest_action_data = (
            _coerce_jsonb(dict(latest_action), "relay_truth", "sensor_status") if latest_action else None
        )
        climate_action_log_age_s = latest_action_data["age_s"] if latest_action_data else None
        climate_action_proof_missing = await conn.fetchval(CLIMATE_ACTION_PROOF_MISSING_SQL)

    climate_age_s = latest_climate["age_s"] if latest_climate else None
    forecast_age_s = forecast_health["age_s"] if forecast_health else None
    data_checks = [
        {
            "check_name": "climate_freshness",
            "status": "ok" if climate_age_s is not None and climate_age_s <= 300 else "fail",
            "metric_value": climate_age_s,
            "threshold_value": 300,
            "details": "climate age seconds",
        },
        {
            "check_name": "forecast_freshness",
            "status": "ok" if forecast_age_s is not None and forecast_age_s <= 21600 else "fail",
            "metric_value": forecast_age_s,
            "threshold_value": 21600,
            "details": "weather_forecast fetched_at age seconds",
        },
        {
            "check_name": "climate_action_log_freshness",
            "status": "ok" if climate_action_log_age_s is not None and climate_action_log_age_s <= 300 else "fail",
            "metric_value": climate_action_log_age_s,
            "threshold_value": 300,
            "details": "controller decision/action snapshot age seconds",
        },
        {
            "check_name": "climate_action_log_proof_complete",
            "status": "ok" if not climate_action_proof_missing else "fail",
            "metric_value": 0 if not climate_action_proof_missing else 1,
            "threshold_value": 0,
            "details": (
                "latest controller proof row has graphable target deltas and relay truth"
                if not climate_action_proof_missing
                else f"missing fields: {climate_action_proof_missing}"
            ),
        },
        {
            "check_name": "open_critical_or_high_alerts",
            "status": "ok" if not open_critical_high else "fail",
            "metric_value": open_critical_high or 0,
            "threshold_value": 0,
            "details": "open critical/high alerts",
        },
    ]
    warning_checks = [
        PublicDataHealthCheck(
            name=r["check_name"],
            status=r["status"],
            metric_value=_to_float(r["metric_value"]),
            threshold_value=_to_float(r["threshold_value"]),
            details=r["details"],
        )
        for r in data_checks
        if r["status"] != "ok"
    ]
    metrics = PublicHomeMetrics(
        generated_at=generated_at,
        greenhouse_id=greenhouse_id,
        climate_rows=climate_summary["climate_rows"] if climate_summary else 0,
        climate_days=climate_summary["climate_days"] if climate_summary else 0,
        active_crops=active_crops or 0,
        plan_count=plan_count or 0,
        lesson_count=lesson_count or 0,
        latest_climate_ts=latest_climate["ts"] if latest_climate else None,
        latest_climate_age_s=latest_climate["age_s"] if latest_climate else None,
        indoor_temp_f=latest_climate["indoor_temp_f"] if latest_climate else None,
        indoor_vpd_kpa=latest_climate["indoor_vpd_kpa"] if latest_climate else None,
        outdoor_temp_f=latest_climate["outdoor_temp_f"] if latest_climate else None,
        outdoor_rh_pct=latest_climate["outdoor_rh_pct"] if latest_climate else None,
        last_plan_id=last_plan["plan_id"] if last_plan else None,
        last_plan_created_at=last_plan["created_at"] if last_plan else None,
        last_plan_age_s=last_plan["age_s"] if last_plan else None,
        planner_score_today=scorecard.get("planner_score"),
        compliance_pct_today=scorecard.get("compliance_pct"),
        cost_today_usd=scorecard.get("cost_total"),
        water_today_gal=scorecard.get("water_gal") or scorecard.get("water_used_gal"),
        open_critical_high_alerts=open_critical_high or 0,
        climate_action_log_age_s=climate_action_log_age_s,
        controller_climate_action=latest_action_data["climate_action"] if latest_action_data else None,
        controller_priority_axis=latest_action_data["priority_axis"] if latest_action_data else None,
        controller_temp_target_delta_f=latest_action_data["temp_target_delta_f"] if latest_action_data else None,
        controller_vpd_target_delta_kpa=latest_action_data["vpd_target_delta_kpa"] if latest_action_data else None,
        controller_temp_band_error_f=latest_action_data["temp_band_error_f"] if latest_action_data else None,
        controller_vpd_band_error_kpa=latest_action_data["vpd_band_error_kpa"] if latest_action_data else None,
        controller_moisture_assist_state=latest_action_data["moisture_assist_state"] if latest_action_data else None,
        controller_wet_assist_allowed=latest_action_data["wet_assist_allowed"] if latest_action_data else None,
        controller_wet_assist_block_reason=latest_action_data["wet_assist_block_reason"]
        if latest_action_data
        else None,
        controller_fog_allowed=latest_action_data["fog_allowed"] if latest_action_data else None,
        controller_fog_block_reason=latest_action_data["fog_block_reason"] if latest_action_data else None,
        controller_relay_truth=latest_action_data["relay_truth"] if latest_action_data else None,
        controller_sensor_status=latest_action_data["sensor_status"] if latest_action_data else None,
        data_health_status=_overall_data_health(data_checks),
        data_health_warnings=warning_checks[:8],
    )
    _PUBLIC_HOME_METRICS_CACHE[cache_key] = (time.monotonic(), metrics)
    return metrics


@app.get("/api/v1/public/evidence-snapshot")
async def public_evidence_snapshot(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Crawler-friendly public proof snapshot for evidence subpages."""
    async with pool.acquire() as conn:
        generated_at = await conn.fetchval("SELECT now()")
        score_rows = await conn.fetch(
            "SELECT metric, value FROM fn_planner_scorecard((now() AT TIME ZONE 'America/Denver')::date)"
        )
        scorecard = {r["metric"]: _to_float(r["value"]) for r in score_rows}
        last_plan = await conn.fetchrow(
            """
            SELECT plan_id,
                   created_at,
                   extract(epoch FROM now() - created_at)::int AS age_s,
                   outcome_score,
                   validated_at,
                   CASE
                     WHEN validated_at IS NOT NULL THEN 'validated'
                     WHEN actual_outcome IS NOT NULL OR outcome_score IS NOT NULL THEN 'evaluated'
                     ELSE 'awaiting outcome'
                   END AS status
            FROM plan_journal
            WHERE greenhouse_id = $1
              AND plan_id LIKE 'iris-%'
              AND plan_id NOT LIKE 'iris-reactive%'
              AND plan_id NOT LIKE 'iris-fix%'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        last_validated_plan = await conn.fetchrow(
            """
            SELECT plan_id, outcome_score, validated_at
            FROM plan_journal
            WHERE greenhouse_id = $1
              AND validated_at IS NOT NULL
              AND plan_id LIKE 'iris-%'
              AND plan_id NOT LIKE 'iris-reactive%'
              AND plan_id NOT LIKE 'iris-fix%'
            ORDER BY validated_at DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        latest_lesson = await conn.fetchrow(
            """
            SELECT id, category, lesson, confidence, times_validated, last_validated
            FROM planner_lessons
            WHERE greenhouse_id = $1 AND is_active
            ORDER BY last_validated DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        latest_climate = await conn.fetchrow(
            """
            SELECT ts, extract(epoch FROM now() - ts)::int AS age_s
            FROM climate
            WHERE greenhouse_id = $1
            ORDER BY ts DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        climate_rows = await conn.fetchval(
            "SELECT count(*)::int FROM climate WHERE greenhouse_id = $1",
            greenhouse_id,
        )
        active_crops = await conn.fetchval(
            """
            SELECT count(DISTINCT crop_catalog_slug)::int
            FROM v_position_current
            WHERE greenhouse_id = $1
              AND is_occupied
              AND crop_catalog_slug IS NOT NULL
            """,
            greenhouse_id,
        )
        plan_count = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM plan_journal
            WHERE greenhouse_id = $1
              AND plan_id LIKE 'iris-%'
              AND plan_id NOT LIKE 'iris-reactive%'
              AND plan_id NOT LIKE 'iris-fix%'
            """,
            greenhouse_id,
        )
        lesson_count = await conn.fetchval(
            "SELECT count(*)::int FROM planner_lessons WHERE greenhouse_id = $1 AND is_active",
            greenhouse_id,
        )
        active_plan = await conn.fetchrow(
            """
            SELECT plan_id,
                   max(created_at) AS created_at,
                   extract(epoch FROM now() - max(created_at))::int AS age_s
            FROM setpoint_plan
            WHERE greenhouse_id = $1
              AND is_active
              AND plan_id IS NOT NULL
            GROUP BY plan_id
            ORDER BY max(created_at) DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        controller_mode = await conn.fetchval(
            "SELECT value FROM system_state WHERE entity = 'greenhouse_state' ORDER BY ts DESC LIMIT 1"
        )
        active_relays = await conn.fetch(
            """
            WITH latest AS (
              SELECT DISTINCT ON (equipment) equipment, state, ts
              FROM equipment_state
              WHERE greenhouse_id = $1
              ORDER BY equipment, ts DESC
            )
            SELECT equipment
            FROM latest
            WHERE state
              AND equipment IN (
                'heat1', 'heat2', 'fan1', 'fan2', 'fog', 'vent',
                'grow_light_main', 'grow_light_grow',
                'mister_south', 'mister_west', 'mister_center',
                'mister_south_fert', 'mister_west_fert',
                'drip_wall', 'drip_center',
                'drip_wall_fert', 'drip_center_fert',
                'fert_master_valve'
              )
            ORDER BY equipment
            """,
            greenhouse_id,
        )
        open_critical_high = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM alert_log
            WHERE greenhouse_id = $1
              AND disposition = 'open'
              AND severity IN ('critical', 'high')
            """,
            greenhouse_id,
        )
        data_checks = await conn.fetch(
            """
            SELECT check_name, lower(status) AS status, metric_value, threshold_value, details
            FROM v_data_trust_ledger
            ORDER BY
              CASE lower(status) WHEN 'fail' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
              check_name
            """
        )
        planner_health = await conn.fetchrow("SELECT * FROM v_planner_trigger_health")

    check_by_name = {r["check_name"]: r for r in data_checks}
    water_check = check_by_name.get("water_accounting_14d")
    water_details = water_check["details"] if water_check else None
    data_health_status = _overall_data_health(data_checks)
    active_plan_id = active_plan["plan_id"] if active_plan else (last_plan["plan_id"] if last_plan else None)
    active_plan_status = None
    if active_plan_id and last_plan and active_plan_id == last_plan["plan_id"]:
        active_plan_status = last_plan["status"]
    elif active_plan_id:
        active_plan_status = "active"
    active_relays_list = [r["equipment"] for r in active_relays]
    water_today_gal = scorecard.get("water_gal") or scorecard.get("water_used_gal")
    planner_health_payload = dict(planner_health) if planner_health else None
    if planner_health_payload and isinstance(planner_health_payload.get("latest_required"), str):
        planner_health_payload["latest_required"] = json.loads(planner_health_payload["latest_required"])
    return {
        "generated_at": generated_at,
        "timezone": "America/Denver",
        "greenhouse_id": greenhouse_id,
        "data_health_status": data_health_status,
        "climate_age_seconds": latest_climate["age_s"] if latest_climate else None,
        "open_critical_high_alerts": open_critical_high or 0,
        "planner_score_today": scorecard.get("planner_score"),
        "both_axis_compliance_pct": scorecard.get("compliance_pct"),
        "temp_compliance_pct": scorecard.get("temp_compliance_pct"),
        "vpd_compliance_pct": scorecard.get("vpd_compliance_pct"),
        "stress_axis_hours": scorecard.get("total_stress_h"),
        "active_plan_id": active_plan_id,
        "active_plan_status": active_plan_status,
        "last_plan_id": last_plan["plan_id"] if last_plan else None,
        "last_validated_plan_id": last_validated_plan["plan_id"] if last_validated_plan else None,
        "cost_today_usd": scorecard.get("cost_total"),
        "water_today_gal": water_today_gal,
        "active_relays": active_relays_list,
        "active_control_crops": active_crops or 0,
        "public_plan_records": plan_count or 0,
        "climate_rows": climate_rows or 0,
        "lesson_rows_active": lesson_count or 0,
        "controller_mode": controller_mode,
        "planner_health": planner_health_payload,
        "planning_quality": {
            "planner_score_today": scorecard.get("planner_score"),
            "both_axis_compliance_pct": scorecard.get("compliance_pct"),
            "temp_compliance_pct": scorecard.get("temp_compliance_pct"),
            "vpd_compliance_pct": scorecard.get("vpd_compliance_pct"),
            "stress_axis_hours": scorecard.get("total_stress_h"),
            "stress_breakdown": {
                "heat_h": scorecard.get("heat_stress_h"),
                "cold_h": scorecard.get("cold_stress_h"),
                "vpd_high_h": scorecard.get("vpd_high_stress_h"),
                "vpd_low_h": scorecard.get("vpd_low_stress_h"),
            },
            "last_validated_plan": dict(last_validated_plan) if last_validated_plan else None,
            "last_plan": dict(last_plan) if last_plan else None,
            "latest_lesson": dict(latest_lesson) if latest_lesson else None,
        },
        "operations": {
            "data_health_status": data_health_status,
            "latest_climate_ts": latest_climate["ts"] if latest_climate else None,
            "latest_climate_age_s": latest_climate["age_s"] if latest_climate else None,
            "open_critical_high_alerts": open_critical_high or 0,
            "active_controller_mode": controller_mode,
            "active_relays": active_relays_list,
            "active_plan_id": active_plan_id,
            "active_plan_status": active_plan_status,
            "active_plan_age_s": active_plan["age_s"] if active_plan else None,
            "last_plan_age_s": last_plan["age_s"] if last_plan else None,
            "cost_today_usd": scorecard.get("cost_total"),
            "water_today_gal": water_today_gal,
            "mister_water_today_gal": scorecard.get("mister_water_gal"),
            "water_accounting_status": water_check["status"] if water_check else None,
            "water_accounting_incomplete": bool(
                water_details and "unattributed water remains" in water_details.lower()
            ),
            "water_accounting_details": water_details,
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# Sprint 23 — Topology + crop-history endpoints
# ═══════════════════════════════════════════════════════════════════════
#
# These endpoints consume the Sprint 22 topology tables and the Sprint 23
# history views (v_position_current, v_crop_history, v_crop_lifecycle).
# The legacy zone:str / position:str-based endpoints above remain in place
# until callers migrate; Phase 4d drops them.


# ── Topology tree (website nav, full-system debug) ────────────────────


@app.get("/api/v1/topology")
async def get_topology(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Full greenhouse → zone → shelf → position tree as JSONB."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT greenhouse_id, greenhouse_name, zones FROM v_topology_tree WHERE greenhouse_id = $1",
            greenhouse_id,
        )
        if row is None:
            raise HTTPException(404, f"Greenhouse '{greenhouse_id}' not found")
    return _coerce_jsonb(dict(row), "zones")


# ── Zone full detail ──────────────────────────────────────────────────


@app.get("/api/v1/zones/{zone_slug}/full")
async def get_zone_full(zone_slug: str, greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Zone detail: shelves[], sensors[], equipment[], water_systems[] (from v_zone_full)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM v_zone_full
            WHERE greenhouse_id = $1 AND zone_slug = $2
            """,
            greenhouse_id,
            zone_slug,
        )
        if row is None:
            raise HTTPException(404, f"Zone '{zone_slug}' not found in '{greenhouse_id}'")
    return _coerce_jsonb(dict(row), "shelves", "sensors", "equipment", "water_systems")


# ── Positions (current state + history) ───────────────────────────────


@app.get("/api/v1/positions", response_model=list[PositionCurrentEntry])
async def list_positions(
    zone_slug: str | None = None,
    occupied_only: bool = False,
    greenhouse_id: str = DEFAULT_GREENHOUSE,
):
    """Every active position + current crop (if any). Empty slots included unless occupied_only=true."""
    sql = "SELECT * FROM v_position_current WHERE greenhouse_id = $1"
    params: list = [greenhouse_id]
    if zone_slug is not None:
        sql += " AND zone_slug = $2"
        params.append(zone_slug)
    if occupied_only:
        sql += " AND is_occupied"
    sql += " ORDER BY zone_slug, shelf_slug, position_label"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [PositionCurrentEntry.model_validate(dict(r)) for r in rows]


@app.get("/api/v1/positions/{position_id}")
async def get_position(position_id: int, greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Position detail: current occupancy + full crop history at this slot."""
    async with pool.acquire() as conn:
        current = await conn.fetchrow(
            "SELECT * FROM v_position_current WHERE position_id = $1 AND greenhouse_id = $2",
            position_id,
            greenhouse_id,
        )
        if current is None:
            raise HTTPException(404, f"Position {position_id} not found")
        history_rows = await conn.fetch(
            "SELECT * FROM v_crop_history WHERE position_id = $1 AND greenhouse_id = $2 ORDER BY planted_date DESC",
            position_id,
            greenhouse_id,
        )
    return {
        "current": dict(current),
        "history": [CropHistoryEntry.model_validate(dict(r)).model_dump() for r in history_rows],
    }


@app.post("/api/v1/positions/{position_id}/plant", status_code=201)
async def plant_at_position(
    position_id: int,
    body: CropCreate,
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    _write_access: None = Depends(require_write_access),
):
    """Create a new crop at a specific position. Validates slot is unoccupied.

    The unique-active-per-position partial index (migration 088) prevents
    double-booking; a collision raises a 409.
    """
    async with pool.acquire() as conn:
        pos = await conn.fetchrow(
            """
            SELECT p.id AS position_id, p.label, sh.zone_id, z.slug AS zone_slug
            FROM positions p JOIN shelves sh ON sh.id = p.shelf_id JOIN zones z ON z.id = sh.zone_id
            WHERE p.id = $1 AND p.greenhouse_id = $2
            """,
            position_id,
            greenhouse_id,
        )
        if pos is None:
            raise HTTPException(404, f"Position {position_id} not found")
        # Resolve crop_catalog_id via slug / name
        catalog_id = None
        if body.crop_catalog_slug:
            catalog_id = await conn.fetchval("SELECT id FROM crop_catalog WHERE slug = $1", body.crop_catalog_slug)
        if catalog_id is None:
            catalog_id = await conn.fetchval(
                "SELECT id FROM crop_catalog WHERE lower(common_name) = lower($1) OR slug = lower($1)",
                body.name,
            )
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO crops (
                    name, variety, position, zone, planted_date, expected_harvest, stage,
                    count, seed_lot_id, supplier, base_temp_f, target_dli, target_vpd_low,
                    target_vpd_high, notes, greenhouse_id,
                    position_id, zone_id, crop_catalog_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
                        $17, $18, $19)
                RETURNING *
                """,
                body.name,
                body.variety,
                pos["label"],
                pos["zone_slug"],
                body.planted_date,
                body.expected_harvest,
                body.stage,
                body.count,
                body.seed_lot_id,
                body.supplier,
                body.base_temp_f,
                body.target_dli,
                body.target_vpd_low,
                body.target_vpd_high,
                body.notes,
                greenhouse_id,
                position_id,
                pos["zone_id"],
                catalog_id,
            )
        except asyncpg.exceptions.UniqueViolationError:
            raise HTTPException(409, f"Position {position_id} ({pos['label']}) is already occupied by an active crop")
    return dict(row)


# ── Crop lifecycle (clear, transplant, harvest) + full timeline ───────


@app.get("/api/v1/crops/{crop_id}/lifecycle", response_model=CropLifecycle)
async def get_crop_lifecycle(crop_id: int, greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Full crop timeline: events array, harvest totals, observations summary."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM v_crop_lifecycle WHERE crop_id = $1 AND greenhouse_id = $2",
            crop_id,
            greenhouse_id,
        )
        if row is None:
            raise HTTPException(404, f"Crop {crop_id} not found")
    return CropLifecycle.model_validate(_coerce_jsonb(dict(row), "events"))


@app.post("/api/v1/crops/{crop_id}/clear")
async def clear_crop(
    crop_id: int,
    operator: str | None = None,
    _write_access: None = Depends(require_write_access),
):
    """Mark a crop as inactive (cleared/removed). Trigger auto-sets cleared_at
    and logs a 'removed' crop_events row."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE crops SET is_active = FALSE WHERE id = $1 AND is_active RETURNING id, cleared_at",
            crop_id,
        )
        if row is None:
            raise HTTPException(404, f"Crop {crop_id} not found or already cleared")
        if operator:
            await conn.execute(
                "UPDATE crop_events SET operator = $1 WHERE crop_id = $2 AND event_type = 'removed' AND operator IS NULL",
                operator,
                crop_id,
            )
    return {"crop_id": crop_id, "is_active": False, "cleared_at": row["cleared_at"]}


class TransplantBody(BaseModel):
    new_position_id: int
    operator: str | None = None
    notes: str | None = None


@app.post("/api/v1/crops/{crop_id}/transplant")
async def transplant_crop(
    crop_id: int,
    body: TransplantBody,
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    _write_access: None = Depends(require_write_access),
):
    """Move a crop to a new position. Logs a 'transplanted' event with old/new position_ids."""
    async with pool.acquire() as conn:
        crop = await conn.fetchrow(
            "SELECT id, position_id, stage, greenhouse_id FROM crops WHERE id = $1 AND is_active",
            crop_id,
        )
        if crop is None:
            raise HTTPException(404, f"Active crop {crop_id} not found")
        target = await conn.fetchrow(
            "SELECT p.id, p.label, sh.zone_id FROM positions p JOIN shelves sh ON sh.id = p.shelf_id WHERE p.id = $1 AND p.greenhouse_id = $2",
            body.new_position_id,
            greenhouse_id,
        )
        if target is None:
            raise HTTPException(404, f"Target position {body.new_position_id} not found")
        try:
            await conn.execute(
                "UPDATE crops SET position_id = $1, zone_id = $2, position = $3 WHERE id = $4",
                body.new_position_id,
                target["zone_id"],
                target["label"],
                crop_id,
            )
        except asyncpg.exceptions.UniqueViolationError:
            raise HTTPException(409, f"Target position {body.new_position_id} is occupied")
        await conn.execute(
            """
            INSERT INTO crop_events (ts, crop_id, event_type, source, operator, notes, greenhouse_id, position_id)
            VALUES (now(), $1, 'transplanted', 'api', $2, $3, $4, $5)
            """,
            crop_id,
            body.operator,
            body.notes or f"Transplanted to position {body.new_position_id}",
            crop["greenhouse_id"],
            body.new_position_id,
        )
    return {"crop_id": crop_id, "new_position_id": body.new_position_id, "new_position_label": target["label"]}


class HarvestBody(BaseModel):
    weight_kg: float | None = None
    unit_count: int | None = None
    quality_grade: str | None = None
    salable_weight_kg: float | None = None
    cull_weight_kg: float | None = None
    cull_reason: str | None = None
    quality_reason: str | None = None
    unit_price: float | None = None
    revenue: float | None = None
    destination: str | None = None
    labor_minutes: int | None = None
    advance_stage: str | None = None  # optional: also update crops.stage
    operator: str | None = None
    notes: str | None = None


@app.post("/api/v1/crops/{crop_id}/harvest", status_code=201)
async def harvest_crop(
    crop_id: int,
    body: HarvestBody,
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    _write_access: None = Depends(require_write_access),
):
    """Record a harvest against this crop. Optionally advance stage."""
    async with pool.acquire() as conn:
        crop = await conn.fetchrow(
            "SELECT id, position_id, zone, stage FROM crops WHERE id = $1 AND greenhouse_id = $2",
            crop_id,
            greenhouse_id,
        )
        if crop is None:
            raise HTTPException(404, f"Crop {crop_id} not found")
        row = await conn.fetchrow(
            """
            INSERT INTO harvests (
                ts, crop_id, weight_kg, unit_count, quality_grade,
                salable_weight_kg, cull_weight_kg, cull_reason, quality_reason,
                unit_price, revenue, destination, labor_minutes,
                zone, operator, notes, greenhouse_id, position_id
            )
            VALUES (now(), $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15, $16, $17)
            RETURNING *
            """,
            crop_id,
            body.weight_kg,
            body.unit_count,
            body.quality_grade,
            body.salable_weight_kg,
            body.cull_weight_kg,
            body.cull_reason,
            body.quality_reason,
            body.unit_price,
            body.revenue,
            body.destination,
            body.labor_minutes,
            crop["zone"],
            body.operator,
            body.notes,
            greenhouse_id,
            crop["position_id"],
        )
        if body.advance_stage and body.advance_stage != crop["stage"]:
            await conn.execute("UPDATE crops SET stage = $1 WHERE id = $2", body.advance_stage, crop_id)
    return dict(row)


# ── Crop catalog ──────────────────────────────────────────────────────


@app.get("/api/v1/crop-catalog")
async def list_crop_catalog():
    """All crop types in the catalog (with aggregated stage/season profiles)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM v_crop_catalog_with_profiles ORDER BY slug")
    return [_coerce_jsonb(dict(r), "stage_season_profiles") for r in rows]


@app.get("/api/v1/crop-catalog/{slug}")
async def get_crop_catalog_entry(slug: str):
    """Single catalog entry + hourly profile detail."""
    async with pool.acquire() as conn:
        entry = await conn.fetchrow("SELECT * FROM v_crop_catalog_with_profiles WHERE slug = $1", slug)
        if entry is None:
            raise HTTPException(404, f"Crop catalog entry '{slug}' not found")
        hours = await conn.fetch(
            """
            SELECT * FROM crop_target_profiles
            WHERE crop_catalog_id = (SELECT id FROM crop_catalog WHERE slug = $1)
            ORDER BY growth_stage, season, hour_of_day
            """,
            slug,
        )
    return {"entry": _coerce_jsonb(dict(entry), "stage_season_profiles"), "hourly_profiles": [dict(h) for h in hours]}


# ── Equipment, switches, sensors (read-only for now) ──────────────────


@app.get("/api/v1/equipment")
async def list_equipment(zone_slug: str | None = None, greenhouse_id: str = DEFAULT_GREENHOUSE):
    sql = """
        SELECT e.*, z.slug AS zone_slug
        FROM equipment e LEFT JOIN zones z ON z.id = e.zone_id
        WHERE e.greenhouse_id = $1 AND e.is_active
    """
    params: list = [greenhouse_id]
    if zone_slug is not None:
        sql += " AND z.slug = $2"
        params.append(zone_slug)
    sql += " ORDER BY e.kind, e.slug"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_coerce_jsonb(dict(r), "specs") for r in rows]


@app.get("/api/v1/switches")
async def list_switches(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Full relay map — v_equipment_relay_map."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM v_equipment_relay_map WHERE greenhouse_id = $1 ORDER BY board, pin",
            greenhouse_id,
        )
    return [dict(r) for r in rows]


@app.get("/api/v1/sensors")
async def list_sensors(zone_slug: str | None = None, greenhouse_id: str = DEFAULT_GREENHOUSE):
    sql = """
        SELECT s.*, z.slug AS zone_slug
        FROM sensors s LEFT JOIN zones z ON z.id = s.zone_id
        WHERE s.greenhouse_id = $1 AND s.is_active
    """
    params: list = [greenhouse_id]
    if zone_slug is not None:
        sql += " AND z.slug = $2"
        params.append(zone_slug)
    sql += " ORDER BY s.kind, s.slug"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


@app.get("/api/v1/pressure-groups/status")
async def pressure_group_status(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Current mister/drip activity per pressure group (v_pressure_group_status)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM v_pressure_group_status WHERE greenhouse_id = $1 ORDER BY group_slug",
            greenhouse_id,
        )
    return [_coerce_jsonb(dict(r), "systems") for r in rows]
