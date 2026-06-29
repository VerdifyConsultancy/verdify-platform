"""
Verdify configuration — single source of truth for all connection settings.

All values come from environment variables (loaded from .env files) with
sensible defaults where appropriate. No secrets have default values.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from slack_config import load_slack_settings

# Load .env files (ingestor-specific, then project-level)
load_dotenv(Path(__file__).parent / ".env")
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Database ──────────────────────────────────────────────────────
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "5432")
DB_NAME = os.environ.get("DB_NAME", "verdify")
DB_USER = os.environ.get("DB_USER", "verdify")
DB_PASS = os.environ.get("DB_PASSWORD") or os.environ.get("POSTGRES_PASSWORD", "")
DB_DSN = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# ── ESP32 ─────────────────────────────────────────────────────────
ESP32_HOST = os.environ.get("ESP32_HOST", "192.168.10.111")
ESP32_PORT = int(os.environ.get("ESP32_PORT", "6053"))
ESP32_API_KEY = os.environ.get("ESP32_API_KEY", "")
EXPECTED_FIRMWARE_VERSION = os.environ.get("EXPECTED_FIRMWARE_VERSION", "")
EXPECTED_FIRMWARE_VERSION_FILE = os.environ.get(
    "EXPECTED_FIRMWARE_VERSION_FILE",
    "/srv/verdify/state/expected-firmware-version",
)

# ── Home Assistant ────────────────────────────────────────────────
HA_URL = os.environ.get("HA_URL", "http://192.168.30.107:8123")
HA_TOKEN_FILE = os.environ.get("HA_TOKEN_FILE", "/mnt/agents/shared/credentials/ha_token.txt")

# ── MQTT (Sentinel occupancy bridge) ─────────────────────────────
MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.30.107")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USER", "")
MQTT_PASS = os.environ.get("MQTT_PASS", "")

# ── MQTT telemetry fan-out bus (#113 / #114) ──────────────────────
# The fan-out bus is a SEPARATE broker from the Sentinel/HAOS occupancy bridge
# above. In the k3s topology prod's ingestor publishes ALL ingested telemetry to
# this broker (publish-all mode, #113); dev/stage ingestors subscribe to it and
# write their OWN per-env DB (subscribe mode, #114). No env reaches Home
# Assistant except prod, and only prod writes the ESP32.
#
# VERDIFY_MQTT_PUBLISH_ALL=1   -> prod publishes every flushed source to the bus.
# VERDIFY_INGEST_SOURCE=mqtt-subscribe -> dev/stage ingest FROM the bus only
#                              (no ESP32 connect-out, no HA, no occupancy bridge).
# Default (unset) preserves today's behaviour: device-side ingest, no fan-out.
#
# Bus connection. Defaults point at the in-cluster broker Service name (#113).
# The fan-out broker creds are separate keys so the occupancy-bridge creds are
# never reused for the cross-env bus.
FANOUT_MQTT_HOST = os.environ.get("FANOUT_MQTT_HOST", "verdify-mqtt")
FANOUT_MQTT_PORT = int(os.environ.get("FANOUT_MQTT_PORT", "1883"))
FANOUT_MQTT_USER = os.environ.get("FANOUT_MQTT_USER", "")
FANOUT_MQTT_PASS = os.environ.get("FANOUT_MQTT_PASS", "")
# Topic root. Per-table, per-greenhouse topics hang off this:
#   {root}/{table}/{greenhouse_id}
FANOUT_MQTT_TOPIC_ROOT = os.environ.get("FANOUT_MQTT_TOPIC_ROOT", "verdify/fanout")

# ── Slack ─────────────────────────────────────────────────────────
SLACK_SETTINGS = load_slack_settings()
SLACK_TOKEN_FILE = os.environ.get("SLACK_TOKEN_FILE", SLACK_SETTINGS.bot_token_file)
SLACK_CHANNEL = os.environ.get("SLACK_CHANNEL", SLACK_SETTINGS.channel_id)

# ── External services ────────────────────────────────────────────
FRIGATE_URL = os.environ.get("FRIGATE_URL", "http://192.168.30.142:5000")
LOKI_URL = os.environ.get("LOKI_URL", "")  # Empty = disabled
GEMINI_API_KEY_FILE = os.environ.get("GEMINI_API_KEY_FILE", "/mnt/agents/shared/credentials/gemini_api_key.txt")

# ── Hermes Iris (sole planner gateway) ───────────────────────────
HERMES_URL = os.environ.get("HERMES_URL", "http://127.0.0.1:8642")
HERMES_API_KEY = os.environ.get("HERMES_IRIS_API_KEY", "")
HERMES_SESSION_PREFIX = os.environ.get("HERMES_SESSION_PREFIX", "hermes:iris:main")

# ── Shadow mode (safe parallel-run guard) ─────────────────────────
# When ON, the ingestor still consumes and parses all telemetry but suppresses
# EVERY write: all DB INSERT/UPDATE/DELETE, all aioesphomeapi number/switch
# device commands, and any state publish. Default OFF == zero behavior change
# to the live single-writer. This is the hard pre-condition for running a
# second (cluster) ingestor against the live DB/ESP32 without double-writing
# telemetry or double-actuating the device. See K3S-2 / design §4.2.
SHADOW_MODE = os.environ.get("VERDIFY_SHADOW_MODE", "").strip().lower() in {"1", "true", "yes", "on"}

# ── Greenhouse ────────────────────────────────────────────────────
GREENHOUSE_ID = os.environ.get("GREENHOUSE_ID", "vallery")
LATITUDE = float(os.environ.get("LATITUDE", "40.1672"))
LONGITUDE = float(os.environ.get("LONGITUDE", "-105.1019"))
TIMEZONE = os.environ.get("TZ", "America/Denver")

# ── Paths ─────────────────────────────────────────────────────────
STATE_DIR = Path(os.environ.get("STATE_DIR", "/srv/verdify/state"))
VAULT_DIR = Path(os.environ.get("VAULT_DIR", "/mnt/iris/verdify-vault"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/mnt/iris/backups"))

# ── Equipment constants ───────────────────────────────────────────
WATTAGES = {
    "heat1": 1500,
    "heat2": 0,  # heat2 is gas (BTU, not watts)
    "fan1": 52,
    "fan2": 52,
    "fog": 1644,  # AquaFog XE 2000 observed draw
    "vent": 10,
    "grow_light_main": 630,
    "grow_light_grow": 816,
}
HEAT2_BTU_PER_HOUR = 75000  # Lennox LF24-75A-5 nameplate
BTU_PER_THERM = 100000

# ── Utility rates ($/unit) ───────────────────────────────────────
ELECTRIC_RATE = float(os.environ.get("ELECTRIC_RATE", "0.111"))  # $/kWh
GAS_RATE = float(os.environ.get("GAS_RATE", "0.83"))  # $/therm
WATER_RATE = float(os.environ.get("WATER_RATE", "0.00484"))  # $/gallon


def get_db_dsn() -> str:
    """Build DB DSN from env vars. Used by standalone scripts."""
    return DB_DSN


def load_token(path: str) -> str:
    """Read and strip a token from a file."""
    return Path(path).read_text().strip()
