"""Opt-in read-only C1 evidence on the ingestor's sole authenticated socket.

Original callback epochs remain separate from experiment recovery receipts and
periodic DB flushes. This module never issues a device setter or a DB mutation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import shared

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER
from verdify_schemas.experiment_config import policy_device_id
from verdify_schemas.tunable_registry import REGISTRY

REQUEST_NAME = "c1-capture-request.json"
RAW_SCHEMA = "verdify-c1-native-source-epoch-v1"
BAND_SLUGS = {
    "temp_low": "consumed_temp_low_f",
    "temp_high": "consumed_temp_high_f",
    "temp_target": "house_temp_target_f",
    "vpd_low": "consumed_vpd_low_kpa",
    "vpd_high": "consumed_vpd_high_kpa",
    "vpd_target": "house_vpd_target_kpa",
}


class NativeCapture:
    """A callback-only collector; reconnects discard every partial epoch."""

    def __init__(self):
        self.identity = None
        self.entities = []
        self.request = None
        self.pending = {}
        self.band = {}
        self.band_pending = {}
        self.epochs = []
        self.last_completed = {}
        self.last_uptime = None
        self.reset_detected = False
        self.blocked_reason = None

    def configure(self, request, *, runtime, generation, entities):
        identity = (request["request_id"], runtime, generation)
        if identity != self.identity:
            self.__init__()
            self.identity = identity
            self.request = request
            self.entities = [asdict(e) for e in entities]

    def record(self, slug, value, *, observed_at, generation):
        if self.blocked_reason is not None:
            return False
        if self.identity is None or generation != self.identity[2]:
            return False
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            return False
        if slug == "uptime_s":
            if isinstance(value, (int, float)) and math.isfinite(value):
                if self.last_uptime is not None and value + 1 < self.last_uptime:
                    self.pending.clear()
                    self.band.clear()
                    self.band_pending.clear()
                    self.epochs.clear()
                    self.reset_detected = True
                self.last_uptime = value
            return False
        if slug == "firmware_version":
            self.band[slug] = {"value": value, "observed_at": observed_at.isoformat(), "slug": slug}
        if slug in {*BAND_SLUGS.values(), "band_source", "consumed_band_sample_epoch"}:
            self.band_pending[slug] = {"value": value, "observed_at": observed_at.isoformat(), "slug": slug}
            if slug == "consumed_band_sample_epoch":
                if all(name in self.band_pending for name in (*BAND_SLUGS.values(), "band_source")):
                    self.band.update(self.band_pending)
                self.band_pending.clear()
        field = next((n for n in CANONICAL_FIELD_ORDER if REGISTRY[n].cfg_readback_object_id == slug), None)
        if field is None:
            return False
        self.pending.pop(field, None)
        if isinstance(value, bool):
            raw = value
        elif isinstance(value, (int, float)) and math.isfinite(value):
            raw = bool(value) if REGISTRY[field].kind == "switch" and value in (0, 1) else value
        else:
            return False
        if field in self.last_completed and observed_at <= self.last_completed[field]:
            return False
        self.pending[field] = {
            "slug": slug,
            "unit": REGISTRY[field].wire_unit or "",
            "observed_at": observed_at.isoformat(),
            "value": raw,
        }
        if set(self.pending) != set(CANONICAL_FIELD_ORDER):
            return False
        moment = max(datetime.fromisoformat(r["observed_at"]) for r in self.pending.values())
        if self.epochs and (moment - datetime.fromisoformat(self.epochs[-1]["completed_at"])).total_seconds() < 30:
            return False
        self.epochs.append(
            {
                "schema": RAW_SCHEMA,
                "source_epoch_id": str(uuid4()),
                "request_id": self.identity[0],
                "completed_at": moment.isoformat(),
                "runtime": {"runtime_instance_id": self.identity[1], "connection_generation": generation},
                "reset_detected": self.reset_detected,
                "observed_components": dict(self.pending),
                "entities": self.entities,
                "band_callbacks": dict(self.band),
            }
        )
        self.last_completed = {n: datetime.fromisoformat(r["observed_at"]) for n, r in self.pending.items()}
        self.pending.clear()
        self.epochs = self.epochs[-2:]
        return True


COLLECTOR = NativeCapture()


def invalidate_for_cached_replay():
    """Any same-socket replay makes an armed request unsuitable for C1 credit."""
    if COLLECTOR.identity is not None:
        COLLECTOR.pending.clear()
        COLLECTOR.band.clear()
        COLLECTOR.band_pending.clear()
        COLLECTOR.epochs.clear()
        COLLECTOR.blocked_reason = "cached_state_replay_during_capture"


def record_native_callback(slug, value, *, observed_at, generation):
    """Only the fenced native subscription calls this; never a snapshot flush."""
    return COLLECTOR.record(slug, value, observed_at=observed_at, generation=generation)


def _atomic(path, value):
    temp = path.with_name("." + path.name + "." + uuid4().hex)
    path.parent.mkdir(parents=True, exist_ok=True)
    with temp.open("x") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, sort_keys=True, default=str, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _source_revisions():
    import entity_map

    import verdify_schemas.tunable_registry as registry_module

    def digest(path):
        return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()

    # Name the scope of the non-secret config digest; do not read credentials.
    context = {
        key: os.environ.get(key, "")
        for key in (
            "GREENHOUSE_ID",
            "VERDIFY_GIT_SHA",
            "VERDIFY_BAND_SOURCE",
            "VERDIFY_POLICY_VECTOR_MODE",
            "VERDIFY_DEVICE_WRITE_ENABLED",
            "ESP32_LOG_LEVEL",
        )
    }
    return {
        "registry_revision": digest(registry_module.__file__),
        "sensor_registry_revision": digest(entity_map.__file__),
        "config_revision": "c1-control-context-v1:sha256:"
        + hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest(),
    }


def _validated_band_sample(epoch, now):
    """Validate exact device computation clock and original callback clocks."""
    bands = epoch["band_callbacks"]
    raw = bands["consumed_band_sample_epoch"]["value"]
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or len(raw) > 10:
        raise ValueError("consumed_band_sample_clock_unavailable")
    seconds = int(raw)
    if not 0 < seconds <= 0xFFFFFFFF:
        raise ValueError("consumed_band_sample_clock_out_of_range")
    sample = datetime.fromtimestamp(seconds, UTC)
    completed = datetime.fromisoformat(epoch["completed_at"])
    if completed.tzinfo is None or completed > now:
        raise ValueError("source_epoch_clock_invalid")
    if not 0 <= (completed - sample).total_seconds() <= 900:
        raise ValueError("consumed_band_sample_clock_not_current")
    for row in bands.values():
        try:
            moment = datetime.fromisoformat(row["observed_at"])
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("band_callback_clock_invalid") from exc
        if moment.tzinfo is None or not 0 <= (completed - moment).total_seconds() <= 900:
            raise ValueError("band_callback_clock_not_current")
    return sample


async def capture_native_source(pool):
    """Two passive callback epochs after initial subscription readiness.

    No SubscribeStates request is issued: its cached replay cannot count as a
    newly computed physical readback. Every cfg route periodically publishes
    its own firmware template sensor through the existing subscription.
    """
    from .component_experiment import RUNTIME_INSTANCE_ID, _component_grid_inventory, component_entity_grid_attestation

    state_dir = Path(os.environ.get("STATE_DIR", "/srv/verdify/state"))
    generation = int(shared.transport_generation)
    evidence = component_entity_grid_attestation()
    _atomic(
        state_dir / "c1-capture-status.json",
        {
            "schema": "verdify-c1-native-capture-status-v1",
            "reported_at": datetime.now(UTC).isoformat(),
            "runtime_instance_id": RUNTIME_INSTANCE_ID,
            "connection_generation": generation,
            "source_revision": os.environ.get("VERDIFY_GIT_SHA"),
            "firmware_revision": evidence.firmware_revision if evidence else None,
            "current_grid_attested": evidence is not None,
            "qualification_claimed": False,
            "revisions": _source_revisions(),
        },
    )
    path = state_dir / REQUEST_NAME
    if not path.exists():
        return
    request = json.loads(path.read_text())
    if request.get("schema") != "verdify-c1-native-capture-request-v1":
        return
    # Fixed state directory and UUID avoid caller-controlled output paths.
    if str(UUID(request["request_id"])) != request["request_id"]:
        return
    now = datetime.now(UTC)
    expiry = datetime.fromisoformat(request["expires_at"])
    if expiry.tzinfo is None or not now < expiry <= now + timedelta(minutes=10):
        return
    if (
        request["runtime_instance_id"] != RUNTIME_INSTANCE_ID
        or request["connection_generation"] != generation
        or request["source_revision"] != os.environ.get("VERDIFY_GIT_SHA")
    ):
        return
    if (
        evidence is None
        or _component_grid_inventory is None
        or not shared.writer_lease_strictly_held()
        or not shared.transport_readbacks_ready(generation)
    ):
        return
    client = shared.esp32.get("client")
    if client is None or shared.esp32.get("state_subscription_client") is not client:
        return
    COLLECTOR.configure(request, runtime=RUNTIME_INSTANCE_ID, generation=generation, entities=_component_grid_inventory)
    output = state_dir / "c1-capture" / request["request_id"]
    if COLLECTOR.blocked_reason is not None:
        _atomic(
            output / "blocked.json",
            {
                "request_id": request["request_id"],
                "reason": COLLECTOR.blocked_reason,
                "qualification_claimed": False,
                "reported_at": now.isoformat(),
            },
        )
        return
    for epoch in COLLECTOR.epochs:
        raw_path = output / (epoch["source_epoch_id"] + ".native.json")
        if not raw_path.exists():
            _atomic(raw_path, epoch)
        input_path = output / (epoch["source_epoch_id"] + ".input.json")
        if input_path.exists():
            continue  # never retime or rewrite an immutable source packet
        bands = epoch["band_callbacks"]
        if not all(
            s in bands for s in (*BAND_SLUGS.values(), "band_source", "consumed_band_sample_epoch", "firmware_version")
        ):
            continue
        if epoch["reset_detected"] or bands["firmware_version"]["value"] != evidence.firmware_revision:
            continue
        try:
            sample = _validated_band_sample(epoch, now)
        except ValueError as error:
            unavailable = output / (epoch["source_epoch_id"] + ".unavailable.json")
            if not unavailable.exists():
                _atomic(
                    unavailable,
                    {
                        "source_epoch_id": epoch["source_epoch_id"],
                        "reason": str(error),
                        "qualification_claimed": False,
                    },
                )
            continue
        # Firmware consumes integer local minutes. Preserve the original sample
        # time separately; evaluate served values for exactly its control minute.
        control_minute = sample.replace(second=0, microsecond=0)
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                served = await conn.fetchrow("SELECT * FROM fn_band_setpoints($1::timestamptz)", control_minute)
                resolver = await conn.fetchval(
                    "SELECT pg_get_functiondef('fn_band_setpoints(timestamptz)'::regprocedure)"
                )
        layers = []
        for series, slug in BAND_SLUGS.items():
            row = bands[slug]
            unit = "°F" if series.startswith("temp") else "kPa"
            # Control is the firmware's published consumed Setpoints member,
            # not an independent recomputation or an inferred legacy scalar.
            layers.append(
                {
                    "series": series,
                    "served": {
                        "value": float(served[series]),
                        "unit": unit,
                        "as_of": control_minute.isoformat(),
                        "source": "fn_band_setpoints(device_sample_control_minute)",
                    },
                    "control": {
                        "value": row["value"],
                        "unit": unit,
                        "as_of": sample.isoformat(),
                        "source": "firmware consumed Setpoints." + series,
                    },
                    "observed": {
                        "value": row["value"],
                        "unit": unit,
                        "as_of": row["observed_at"],
                        "source": "original fenced native callback",
                        "slug": slug,
                    },
                }
            )
        document = {
            "schema": "verdify-component-grid-capture-input-v2",
            "device_id": policy_device_id(os.environ.get("GREENHOUSE_ID", "vallery")),
            "observed_at": epoch["completed_at"],
            "runtime": epoch["runtime"],
            "revisions": {
                **_source_revisions(),
                "source_revision": request["source_revision"],
                "firmware_revision": evidence.firmware_revision,
                "crop_band_resolver_revision": "sha256:" + hashlib.sha256(resolver.encode()).hexdigest(),
            },
            "entities": epoch["entities"],
            "observed_components": epoch["observed_components"],
            "band_layers": layers,
            "band_source": {
                "slug": "band_source",
                "value": bands["band_source"]["value"],
                "as_of": bands["band_source"]["observed_at"],
                **epoch["runtime"],
            },
        }
        _atomic(input_path, document)
    # Collection continues passively. Output files retain source UUIDs; periodic
    # task calls cannot synthesize or retime another source epoch.
