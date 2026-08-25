"""Fail-closed environment contract for the three orchestrator modes."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import ContractError, OrchestratorMode, require_sha256, require_uuid

CAPABILITY_ENV = "VERDIFY_COMPONENT_EXPERIMENT_ENABLED"
VECTOR_MODE_ENV = "VERDIFY_POLICY_VECTOR_MODE"
ACTIVE_EXPERIMENT_ENV = "VERDIFY_ACTIVE_EXPERIMENT_ID"
MODE_ENV = "VERDIFY_EXPERIMENT_V2_MODE"
DEFAULT_POLL_INTERVAL_SECONDS = 15.0
CORTEX_ENDPOINT_HOST = "cortex.vallery.net"
CORTEX_ENDPOINT_ADDRESS = ipaddress.ip_address("192.168.7.10")

_DB_CREDENTIAL_ENV = {
    OrchestratorMode.LIFECYCLE: (
        "VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_USER",
        "VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_PASSWORD",
    ),
    OrchestratorMode.SELECTOR: (
        "VERDIFY_EXPERIMENT_RANDOMIZER_DB_USER",
        "VERDIFY_EXPERIMENT_RANDOMIZER_DB_PASSWORD",
    ),
    OrchestratorMode.FREEZER: (
        "VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_USER",
        "VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_PASSWORD",
    ),
}

_DUTY_ROLE = {
    OrchestratorMode.LIFECYCLE: "verdify_experiment_shadow_scheduler",
    OrchestratorMode.SELECTOR: "verdify_experiment_randomizer",
    OrchestratorMode.FREEZER: "verdify_experiment_outcome_freezer",
}

_DEPLOYMENT_LOGIN = {
    OrchestratorMode.LIFECYCLE: "verdify_experiment_v2_shadow_scheduler_login",
    OrchestratorMode.SELECTOR: "verdify_experiment_v2_randomizer_login",
    OrchestratorMode.FREEZER: "verdify_experiment_v2_outcome_freezer_login",
}


class ConfigurationError(ContractError):
    """Configuration is contradictory or unsafe; no runtime call is allowed."""


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)
    duty_role: str = ""
    statement_timeout_ms: int = 10_000


@dataclass(frozen=True)
class ProviderSettings:
    endpoint: str
    endpoint_host: str
    endpoint_port: int
    egress_network: ipaddress.IPv4Network | ipaddress.IPv6Network
    api_key: str = field(repr=False)
    maximum_response_bytes: int = 16_384


@dataclass(frozen=True)
class RuntimeSettings:
    mode: OrchestratorMode
    active_experiment_id: str | None
    database: DatabaseSettings | None
    provider: ProviderSettings | None
    selector_identity_path: Path | None
    lifecycle_plan_path: Path | None
    lifecycle_plan_sha256: str | None
    outcome_identity_path: Path | None
    poll_interval_seconds: float
    inactive_reason: str | None

    @property
    def runnable(self) -> bool:
        return self.active_experiment_id is not None and self.database is not None and self.inactive_reason is None


def _bounded_int(raw: str, *, field_name: str, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{field_name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{field_name} must be in [{minimum},{maximum}]")
    return value


def _bounded_float(raw: str, *, field_name: str, minimum: float, maximum: float) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{field_name} must be numeric") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{field_name} must be in [{minimum},{maximum}]")
    return value


def _provider_settings(env: Mapping[str, str]) -> tuple[ProviderSettings | None, Path | None]:
    endpoint = env.get("VERDIFY_EXPERIMENT_SELECTOR_ENDPOINT", "").strip()
    egress_cidr = env.get("VERDIFY_EXPERIMENT_SELECTOR_EGRESS_CIDR", "").strip()
    api_key = env.get("VERDIFY_EXPERIMENT_SELECTOR_API_KEY", "")
    identity_path_raw = env.get("VERDIFY_EXPERIMENT_SELECTOR_IDENTITY_PATH", "").strip()
    identity_path = Path(identity_path_raw) if identity_path_raw else None
    # The checked-in component deliberately carries a non-routable placeholder
    # CIDR so Kustomize can inject one field into the NetworkPolicy.  An empty
    # endpoint remains an explicitly unconfigured provider regardless of that
    # placeholder, and therefore means baseline/no network I/O.
    if not endpoint:
        return None, identity_path
    if not egress_cidr:
        raise ConfigurationError("configured selector endpoint requires one exact egress CIDR")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or not parsed.path.startswith("/")
    ):
        raise ConfigurationError("selector endpoint must be one credential-free exact HTTPS URL")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ConfigurationError("selector endpoint port is invalid") from exc
    if port != 443:
        raise ConfigurationError("selector endpoint must use TCP 443")
    try:
        network = ipaddress.ip_network(egress_cidr, strict=True)
    except ValueError as exc:
        raise ConfigurationError("selector egress CIDR must be one exact IP host") from exc
    if network.num_addresses != 1:
        raise ConfigurationError("selector egress CIDR must be /32 or /128")
    address = network.network_address
    cortex_service_endpoint = parsed.hostname == CORTEX_ENDPOINT_HOST and address == CORTEX_ENDPOINT_ADDRESS
    if not address.is_global and not cortex_service_endpoint:
        raise ConfigurationError("selector egress IP must be globally routable and outside device/private ranges")
    maximum_response_bytes = _bounded_int(
        env.get("VERDIFY_EXPERIMENT_SELECTOR_MAX_RESPONSE_BYTES", "16384"),
        field_name="selector maximum response bytes",
        minimum=256,
        maximum=65_536,
    )
    if not api_key:
        return None, identity_path
    return ProviderSettings(endpoint, parsed.hostname, port, network, api_key, maximum_response_bytes), identity_path


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    mode_override: str | None = None,
) -> RuntimeSettings:
    """Load settings without assigning unsafe defaults.

    Capability off produces a Ready no-op configuration.  An empty active
    experiment or absent dedicated database credentials keeps the process
    non-actuating but intentionally unready.  Contradictory values raise before
    a database or network client can be constructed.
    """

    source = os.environ if env is None else env
    mode_raw = mode_override or source.get(MODE_ENV, "")
    try:
        mode = OrchestratorMode(mode_raw)
    except ValueError as exc:
        raise ConfigurationError("orchestrator mode must be lifecycle, selector, or freezer") from exc
    capability = source.get(CAPABILITY_ENV, "off")
    if capability == "off":
        return RuntimeSettings(
            mode,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            DEFAULT_POLL_INTERVAL_SECONDS,
            "capability_off",
        )
    if capability != "enabled":
        raise ConfigurationError(f"{CAPABILITY_ENV} must be exactly off or enabled")
    if source.get(VECTOR_MODE_ENV, "off") != "off":
        raise ConfigurationError("component experiment requires generalized vector mode exactly off")
    experiment_raw = source.get(ACTIVE_EXPERIMENT_ENV, "").strip()
    if not experiment_raw:
        return RuntimeSettings(
            mode,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            DEFAULT_POLL_INTERVAL_SECONDS,
            "experiment_unconfigured",
        )
    experiment_id = require_uuid(experiment_raw, ACTIVE_EXPERIMENT_ENV)
    user_env, password_env = _DB_CREDENTIAL_ENV[mode]
    user = source.get(user_env, "").strip()
    password = source.get(password_env, "")
    if not user and not password:
        return RuntimeSettings(
            mode,
            experiment_id,
            None,
            None,
            None,
            None,
            None,
            None,
            DEFAULT_POLL_INTERVAL_SECONDS,
            "database_unconfigured",
        )
    if user == _DEPLOYMENT_LOGIN[mode] and not password:
        # Kubernetes supplies the non-secret, duty-specific username directly
        # and projects only the password from an optional named Secret. Secret
        # absence is therefore distinguishable from a malformed custom pair.
        return RuntimeSettings(
            mode,
            experiment_id,
            None,
            None,
            None,
            None,
            None,
            None,
            DEFAULT_POLL_INTERVAL_SECONDS,
            "database_unconfigured",
        )
    if not user or not password:
        raise ConfigurationError("dedicated database credential is incomplete")
    ordinary_user = source.get("DB_USER", "verdify").strip()
    if user == ordinary_user:
        raise ConfigurationError("orchestrator database login must differ from the ordinary application login")
    poll_interval = _bounded_float(
        source.get("VERDIFY_EXPERIMENT_V2_POLL_INTERVAL_SECONDS", "15"),
        field_name="poll interval",
        minimum=1.0,
        maximum=300.0,
    )
    host = source.get("DB_HOST", "").strip()
    database = source.get("DB_NAME", "").strip()
    if not host or not database:
        raise ConfigurationError("database host and name must be explicitly configured")
    port = _bounded_int(source.get("DB_PORT", "5432"), field_name="database port", minimum=1, maximum=65_535)
    statement_timeout_ms = _bounded_int(
        source.get("VERDIFY_EXPERIMENT_V2_DB_STATEMENT_TIMEOUT_MS", "10000"),
        field_name="database statement timeout",
        minimum=1_000,
        maximum=60_000,
    )
    database_settings = DatabaseSettings(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        duty_role=_DUTY_ROLE[mode],
        statement_timeout_ms=statement_timeout_ms,
    )
    provider: ProviderSettings | None = None
    identity_path: Path | None = None
    lifecycle_plan_path: Path | None = None
    lifecycle_plan_sha256: str | None = None
    outcome_identity_path: Path | None = None
    if mode is OrchestratorMode.SELECTOR:
        provider, identity_path = _provider_settings(source)
    elif mode is OrchestratorMode.LIFECYCLE:
        lifecycle_path_raw = source.get("VERDIFY_EXPERIMENT_V2_LIFECYCLE_PLAN_PATH", "").strip()
        lifecycle_hash_raw = source.get("VERDIFY_EXPERIMENT_V2_LIFECYCLE_PLAN_SHA256", "").strip()
        if bool(lifecycle_path_raw) != bool(lifecycle_hash_raw):
            # A path without an immutable digest (including the checked-in
            # optional mount path) is safely treated as no scheduling plan.
            lifecycle_plan_path = None
            lifecycle_plan_sha256 = None
        elif lifecycle_path_raw:
            lifecycle_plan_path = Path(lifecycle_path_raw)
            lifecycle_plan_sha256 = require_sha256(
                lifecycle_hash_raw,
                "VERDIFY_EXPERIMENT_V2_LIFECYCLE_PLAN_SHA256",
            )
    elif mode is OrchestratorMode.FREEZER:
        outcome_path_raw = source.get("VERDIFY_EXPERIMENT_V2_OUTCOME_IDENTITY_PATH", "").strip()
        outcome_identity_path = Path(outcome_path_raw) if outcome_path_raw else None
    return RuntimeSettings(
        mode,
        experiment_id,
        database_settings,
        provider,
        identity_path,
        lifecycle_plan_path,
        lifecycle_plan_sha256,
        outcome_identity_path,
        poll_interval,
        None,
    )
