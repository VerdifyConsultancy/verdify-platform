"""External-API wire schemas — upstream payloads, validated at our boundary.

Four integrations today:
- Cortex /v1/embeddings (embed.main, #1351): the knowledge corpus and MCP
  semantic search (verdify_schemas/embeddings.py).
- Open-Meteo 16-day hourly forecast (forecast-sync.py; 27 parallel arrays)
- Home Assistant /api/states/{entity_id} (Shelly, Tempest, hydro YINMIK,
  switches, occupancy).
- The YINMIK hydroponic meter's Tuya local-protocol status() payload
  (ingestor/hydro_probe.py, VD-70).

The existing ingestion paths parse these ad hoc. Wrapping them in a Pydantic
model at the boundary catches length-mismatch and missing-key drift when
Open-Meteo changes its response or HA renames an entity.
"""

from __future__ import annotations

from datetime import datetime as DateTime
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)


class OpenMeteoHourly(BaseModel):
    """`hourly` block from the Open-Meteo /v1/forecast response.

    Open-Meteo emits parallel arrays keyed by variable name. `time` drives
    the length; every other array must match. A mismatch here today zips
    silently; with this schema it fails loud.
    """

    model_config = ConfigDict(extra="ignore")

    time: list[str]  # ISO8601 timestamps (Open-Meteo returns as strings)
    temperature_2m: list[float | None] | None = None
    relative_humidity_2m: list[float | None] | None = None
    dew_point_2m: list[float | None] | None = None
    apparent_temperature: list[float | None] | None = None
    precipitation_probability: list[float | None] | None = None
    precipitation: list[float | None] | None = None
    rain: list[float | None] | None = None
    snowfall: list[float | None] | None = None
    weather_code: list[int | None] | None = None
    cloud_cover: list[float | None] | None = None
    cloud_cover_low: list[float | None] | None = None
    cloud_cover_high: list[float | None] | None = None
    wind_speed_10m: list[float | None] | None = None
    wind_direction_10m: list[float | None] | None = None
    wind_gusts_10m: list[float | None] | None = None
    shortwave_radiation: list[float | None] | None = None
    direct_radiation: list[float | None] | None = None
    diffuse_radiation: list[float | None] | None = None
    uv_index: list[float | None] | None = None
    sunshine_duration: list[float | None] | None = None
    surface_pressure: list[float | None] | None = None
    et0_fao_evapotranspiration: list[float | None] | None = None
    soil_temperature_0cm: list[float | None] | None = None
    visibility: list[float | None] | None = None
    vapour_pressure_deficit: list[float | None] | None = None

    @model_validator(mode="after")
    def _validate_parallel_lengths(self) -> OpenMeteoHourly:
        n = len(self.time)
        for name, arr in self.model_dump().items():
            if name == "time" or arr is None:
                continue
            if not isinstance(arr, list):
                continue
            if len(arr) != n:
                raise ValueError(f"Open-Meteo hourly.{name} length {len(arr)} != hourly.time length {n}")
        return self


class OpenMeteoForecastResponse(BaseModel):
    """Top-level Open-Meteo response envelope."""

    model_config = ConfigDict(extra="ignore")

    latitude: float | None = None
    longitude: float | None = None
    elevation: float | None = None
    generationtime_ms: float | None = None
    utc_offset_seconds: int | None = None
    timezone: str | None = None
    timezone_abbreviation: str | None = None
    hourly: OpenMeteoHourly
    hourly_units: dict | None = None


# ── Home Assistant REST API shape ──


class HAEntityState(BaseModel):
    """HA /api/states/{entity_id} response.

    HA entities can report `state` as a numeric string, 'unavailable',
    'unknown', or a text label (mode names, enum values). The ingestor
    currently `_parse_float()`s everything and silently drops failures.
    This schema surfaces both the raw state + attributes so downstream
    decides what to do with non-numeric values.
    """

    model_config = ConfigDict(extra="ignore")

    entity_id: str = Field(..., min_length=1)
    state: str  # Always string on the wire — caller parses per-entity
    attributes: dict = Field(default_factory=dict)
    last_changed: AwareDatetime | None = None
    last_updated: AwareDatetime | None = None

    @property
    def is_available(self) -> bool:
        return self.state not in ("unavailable", "unknown", "", "None")

    def as_float(self) -> float | None:
        """Parse state as float if possible; return None for unavailable / non-numeric."""
        if not self.is_available:
            return None
        try:
            return float(self.state)
        except (TypeError, ValueError):
            return None

    def as_datetime(self) -> DateTime | None:
        if not self.is_available:
            return None
        try:
            return DateTime.fromisoformat(self.state.replace(" ", "T"))
        except (TypeError, ValueError):
            return None


# ── Tuya local protocol (YINMIK hydroponic meter, VD-70) ──

TuyaDpId = Annotated[str, Field(pattern=r"^[0-9]+$")]
TuyaDpValue = StrictBool | StrictInt | StrictFloat | StrictStr


class TuyaDeviceStatus(BaseModel):
    """Tuya local-protocol ``status()`` payload, as tinytuya returns it.

    ``dps`` maps a data-point id (a numeric string) to the device's raw value:
    an int, float, bool or str, never coerced. Envelope fields such as the
    device id are ignored, so they are never carried past this boundary.
    """

    model_config = ConfigDict(extra="ignore")

    dps: dict[TuyaDpId, TuyaDpValue]


# ── Cortex embeddings (embed.main, #1351) ──


class CortexEmbeddingDatum(BaseModel):
    """One ``data`` row of an OpenAI-compatible ``/v1/embeddings`` response."""

    model_config = ConfigDict(extra="ignore")

    index: StrictInt = Field(ge=0)
    embedding: list[StrictFloat | StrictInt] = Field(min_length=1)


class CortexEmbeddingResponse(BaseModel):
    """Cortex (GPUStack) ``/v1/embeddings`` response.

    Only the vectors and their input positions cross this boundary. The
    dimension and finiteness checks live with the contract in
    verdify_schemas/embeddings.py, which knows the expected size.
    """

    model_config = ConfigDict(extra="ignore")

    data: list[CortexEmbeddingDatum] = Field(min_length=1)
