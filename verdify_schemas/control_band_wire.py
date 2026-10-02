"""Independent desired-anchor wire projection; never accepts consumed band values.

Mirror the delivered greenhouse_solar.h/controls.yaml binary32 expression order.
The ideal SQL corridor remains separate. This projection is read-only evidence,
not planner policy, a device setter, or a tolerance around an observed value.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import json
import math
import struct
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

FIRMWARE_SOURCES = {
    "firmware/lib/greenhouse_solar.h": "dd0557760ac17fd61eca8a0449d0060f8452d418616edda2ab95892408a2fb0e",
    "firmware/greenhouse/controls.yaml": "db066c4708bf33ac11014ae1d006a37d26098bbf6e17720312db3f7c36774ac3",
}
SERIES = ("temp_low", "temp_high", "temp_target", "vpd_low", "vpd_high", "vpd_target")
ANCHORS = ("sr", "sm", "ss", "mid")
ANCHOR_PARAMS = tuple(f"band_{series}_{anchor}" for series in SERIES for anchor in ANCHORS)
CONTRACT_SCHEMA = "verdify-c1-served-wire-contract-v1"
SITE = {"latitude_deg": 40.167, "longitude_deg": -105.102, "timezone": "America/Denver"}


class F(float):
    """One C++ float operation per operator, including intermediate rounding."""

    def __new__(cls, value):
        return super().__new__(cls, struct.unpack("!f", struct.pack("!f", float(value)))[0])

    def __add__(self, other):
        return F(float(self) + float(other))

    __radd__ = __add__

    def __sub__(self, other):
        return F(float(self) - float(other))

    def __rsub__(self, other):
        return F(float(other) - float(self))

    def __mul__(self, other):
        return F(float(self) * float(other))

    __rmul__ = __mul__

    def __truediv__(self, other):
        return F(float(self) / float(other))

    def __rtruediv__(self, other):
        return F(float(other) / float(self))

    def __neg__(self):
        return F(-float(self))


# Use the native float functions, not double sin/cos followed by rounding.
# The differential gate runs these expressions against the owning C++ header.
_libm = ctypes.CDLL(ctypes.util.find_library("m") or ctypes.util.find_library("System"))
for _name in ("sinf", "cosf", "acosf"):
    _fn = getattr(_libm, _name)
    _fn.argtypes = [ctypes.c_float]
    _fn.restype = ctypes.c_float


_libm.fmaf.argtypes = [ctypes.c_float, ctypes.c_float, ctypes.c_float]
_libm.fmaf.restype = ctypes.c_float


def fma(a, b, c):
    return F(_libm.fmaf(F(a), F(b), F(c)))


def sin(x):
    return F(_libm.sinf(F(x)))


def cos(x):
    return F(_libm.cosf(F(x)))


def acos(x):
    return F(_libm.acosf(F(x)))


def algorithm_revision() -> str:
    root = Path(__file__).resolve().parents[1]
    for name, expected in FIRMWARE_SOURCES.items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("served wire firmware source contract changed: " + name)
    payload = {
        "schema": CONTRACT_SCHEMA,
        "sources": FIRMWARE_SOURCES,
        "site": SITE,
        "precision": {"temp": 1, "vpd": 2},
        "math_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    return "served-band-wire-v1:sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def solar_times(doy: int, offset: int) -> tuple[int, int, int]:
    pi = F(3.14159265358979)
    g = F(2) * pi / F(365) * (F(max(1, min(366, doy))) - F(1))
    cg, sg = cos(g), sin(g)
    c2g, s2g = cos(F(2) * g), sin(F(2) * g)
    eq = fma(F(0.001868), cg, F(0.000075))
    eq = fma(-F(0.032077), sg, eq)
    eq = fma(-F(0.014615), c2g, eq)
    eq = fma(-F(0.040849), s2g, eq)
    decl = fma(-F(0.399912), cg, F(0.006918))
    decl = fma(F(0.070257), sg, decl)
    decl = fma(-F(0.006758), c2g, decl)
    decl = fma(F(0.000907), s2g, decl)
    decl = fma(-F(0.002697), cos(F(3) * g), decl)
    decl = fma(F(0.001480), sin(F(3) * g), decl)
    lat = F(SITE["latitude_deg"]) * pi / F(180)
    # Measured620a and historical2143 use these explicit madd/msub orders.
    zenith_cos = F(-0.014538058079779148)
    ha = fma(-sin(lat), sin(decl), zenith_cos) / (cos(lat) * cos(decl))
    ha = F(max(-1, min(1, ha)))
    degrees = acos(ha) * F(180) / pi
    noon = fma(-F(SITE["longitude_deg"]), F(4), F(720))
    noon = fma(-eq, F(229.18), noon)

    def local(x):
        # std::lround is half away from zero; Python round is ties to even.
        rounded = math.floor(float(x) + 0.5) if x >= 0 else math.ceil(float(x) - 0.5)
        return (rounded + offset) % 1440

    return local(fma(-degrees, F(4), noon)), local(noon), local(fma(degrees, F(4), noon))


def solar_phase(minute: int, times: tuple[int, int, int]) -> F:
    sr, sm, ss = times
    if sm < sr:
        sm += 1440
    if ss < sm:
        ss += 1440
    nsr = sr + 1440
    mid = ss + (nsr - ss) // 2
    m = F(minute % 1440)
    if m < sr:
        m += F(1440)
    sr, sm, ss, mid, nsr = map(F, (sr, sm, ss, mid, nsr))
    r0 = F(1) / max(sm - sr, F(0.000001))
    r1 = F(1) / max(ss - sm, F(0.000001))
    r2 = F(1) / max(mid - ss, F(0.000001))
    r3 = F(1) / max(nsr - mid, F(0.000001))
    dsr = F(0.5) * (r3 + r0)
    dsm = F(0.5) * (r0 + r1)
    dss = F(0.5) * (r1 + r2)
    dmid = F(0.5) * (r2 + r3)

    def herm(a, b, pa, da, db):
        if b <= a:
            return pa
        length = b - a
        u = (m - a) / length
        u2 = u * u
        u3 = u2 * u
        base = pa + fma(-u3, F(2), F(3) * u2)
        shape = u + fma(-u2, F(2), u3)
        return fma(db * length, u3 - u2, fma(da * length, shape, base))

    if m <= sm:
        p = herm(sr, sm, F(0), dsr, dsm)
    elif m <= ss:
        p = herm(sm, ss, F(1), dsm, dss)
    elif m <= mid:
        p = herm(ss, mid, F(2), dss, dmid)
    else:
        p = herm(mid, nsr, F(3), dmid, dsr)
    return F(0) if p < 0 or p >= 4 else p


def band_value(anchors, phase) -> F:
    sr, sm, ss, mid = map(F, anchors)
    phase = F(phase)
    phase = fma(-F(4), F(math.floor(phase / F(4))), phase)
    theta = F(3.14159265358979) * phase * F(0.5)
    c1 = (sr - ss) * F(0.5)
    s1 = (sm - mid) * F(0.5)
    c2 = (sr - sm + ss - mid) * F(0.25)
    return fma(c2, cos(F(2) * theta), fma(s1, sin(theta), fma(sr + sm + ss + mid, F(0.25), c1 * cos(theta))))


def resolve_served_wire(desired: dict, sample: datetime, night_bias: float) -> dict:
    if sample.tzinfo is None or set(desired) != set(ANCHOR_PARAMS):
        raise ValueError("served wire requires exact desired house anchors and a dated sample")
    if any(isinstance(v, bool) or not math.isfinite(float(v)) for v in desired.values()):
        raise ValueError("served wire desired anchors must be finite numbers")
    if isinstance(night_bias, bool) or not math.isfinite(float(night_bias)) or not 0 <= night_bias <= 0.25:
        raise ValueError("served wire source night bias is outside its contract")
    rounded = {n: round(float(v), 1 if n.startswith("band_temp_") else 2) for n, v in desired.items()}
    local = sample.astimezone(ZoneInfo(SITE["timezone"]))
    offset = int(local.utcoffset().total_seconds() // 60)
    minute = local.hour * 60 + local.minute
    times = solar_times(local.timetuple().tm_yday, offset)
    phase = solar_phase(minute, times)
    values = {s: band_value([rounded[f"band_{s}_{a}"] for a in ANCHORS], phase) for s in SERIES}
    bias = F(night_bias)
    if bias > 0 and 2 <= phase <= 4:
        s = sin(F(3.14159265) * (phase - F(2)) * F(0.5))
        add = bias * s * s
        for key in ("vpd_low", "vpd_high", "vpd_target"):
            values[key] += add
    return {
        "values": {n: float(v) for n, v in values.items()},
        "rounded_anchors": rounded,
        "clock": {
            "sample_at": sample.isoformat(),
            "local_date": local.date().isoformat(),
            "local_control_minute": minute,
            "day_of_year": local.timetuple().tm_yday,
            "utc_offset_min": offset,
            "solar_times": dict(zip(("sunrise_min", "solar_noon_min", "sunset_min"), times, strict=True)),
            "solar_phase": float(phase),
        },
    }


SUPPORTED_FIRMWARE = "2026.10.2.0637.620a218a-wifi-bound"
TARGET_ELF_SHA256 = "39a237cfb044e72633d52646c203d27b1217ba9f9de3d4a8308798b1b7a96b5b"
SQL_FUNCTIONS = (
    "fn_band_setpoints(timestamptz)",
    "fn_crop_band_value(text,text,timestamptz,text,text,text)",
    "fn_solar_phase(timestamptz)",
    "fn_solar_sunrise_hour(timestamptz)",
    "fn_solar_sunset_hour(timestamptz)",
    "fn_current_season()",
    "fn_hermite_phase(double precision,double precision,double precision,double precision,double precision,double precision)",
)


IDEAL_SQL_FUNCTION_SHA256 = {
    "fn_band_setpoints(timestamptz)": "58a0e95e646a5de45c022438546608e1677adad000800c6784e0f6316241b281",
    "fn_crop_band_value(text,text,timestamptz,text,text,text)": "e5682cb83e81c57ee44254c555d775e4f7dedf1078af3ba98ccc97f71c03d9e0",
    "fn_current_season()": "c35ff6fc0aa046b208da7ff7a9f03a9fcba658d0ed652c01b08f0b826e544130",
    "fn_hermite_phase(double precision,double precision,double precision,double precision,double precision,double precision)": "f9642002e69bbac2d1a2dc7f9a19fb069ea86295c995713eaf37c8efa2e164fb",
    "fn_solar_phase(timestamptz)": "960f6e4c435ce534db8956630bd32408a3d1294362b2aef44c018189d250e715",
    "fn_solar_sunrise_hour(timestamptz)": "66795353790f6678c02f8f6af5e6d576fe8177714b07a679925360bd1f851d51",
    "fn_solar_sunset_hour(timestamptz)": "227310e3542f7e0bc06f0639724a72444a8366320cb30da07ecfc01f185a9c79",
}


def content_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def selected_house_anchors(rows: list[dict], season: str) -> dict:
    """Exact current dispatcher row precedence; reject ambiguous/default anchors.

    Dispatcher overlays house rows by season score. Unlike its cold-start path,
    this evidence resolver never fills absent rows from registry defaults.
    """
    selected = {}
    scores = {}
    for row in rows:
        if str(row.get("crop_type") or "").strip().lower() != "house":
            continue
        key = f"band_{str(row.get('series') or '').strip().lower()}_{str(row.get('anchor') or '').strip().lower()}"
        if key not in ANCHOR_PARAMS:
            continue
        row_season = str(row.get("season") or "").strip().lower()
        score = 2 if row_season == season else 1 if row_season in ("", "all", "any") else 0
        if not score:
            continue
        if score == scores.get(key):
            raise ValueError("ambiguous selected desired anchor: " + key)
        if score > scores.get(key, 0):
            selected[key] = row["value"]
            scores[key] = score
    if set(selected) != set(ANCHOR_PARAMS):
        raise ValueError("incomplete selected desired house anchors")
    return selected


def wire_resolver_revision(contract: dict) -> str:
    payload = {
        key: contract[key]
        for key in (
            "schema",
            "algorithm_revision",
            "target_elf_sha256",
            "worksheet",
            "season",
            "anchor_rows",
            "sql_functions",
            "database",
        )
    }
    return "served-band-wire-v1:sha256:" + content_digest(payload)


def validate_wire_contract(contract: dict, document: dict) -> dict:
    """Recompute independent expected values and verify complete v3 lineage.

    This validates source evidence, not signatures or hardware authenticity.
    Original callback custody remains an independent qualification requirement.
    """
    from verdify_schemas.c1_grid_projection import project_c1_grid_state
    from verdify_schemas.tunable_registry import REGISTRY

    keys = {
        "schema",
        "algorithm_revision",
        "target_elf_sha256",
        "worksheet",
        "query_at",
        "season",
        "anchor_rows",
        "anchor_callbacks",
        "sample_epoch_callback",
        "ideal_sql",
        "sql_functions",
        "database",
    }
    if set(contract) != keys or contract["schema"] != CONTRACT_SCHEMA:
        raise ValueError("incomplete served wire evidence contract")
    if contract["algorithm_revision"] != algorithm_revision() or contract["target_elf_sha256"] != TARGET_ELF_SHA256:
        raise ValueError("served wire arithmetic source/target changed")
    worksheet = contract["worksheet"]
    limits = {
        "verdify-c1-qualification-worksheet-v1": 12,
        "verdify-c1-qualification-worksheet-v2": 48,
        "verdify-c1-qualification-worksheet-v3": 48,
    }
    if worksheet.get("schema") not in limits or not 1 <= len(worksheet["decisions"]) <= limits[worksheet["schema"]]:
        raise ValueError("served wire source worksheet scope invalid")
    if str(UUID(worksheet["worksheet_id"])) != worksheet["worksheet_id"]:
        raise ValueError("served wire worksheet identity invalid")
    preview = worksheet["preview"]
    identity = preview["identity"]
    for key in ("runtime_instance_id", "connection_generation"):
        if identity[key] != document["runtime"][key]:
            raise ValueError("served wire worksheet runtime changed")
    for key in ("firmware_revision", "source_revision"):
        if identity[key] != document["revisions"][key]:
            raise ValueError("served wire worksheet source changed")
    if identity["firmware_revision"] != SUPPORTED_FIRMWARE:
        raise ValueError("served wire target lowering is not qualified for this firmware")
    if content_digest(preview["base_inputs"]) != preview["base_inputs_sha256"]:
        raise ValueError("served wire source policy hash changed")
    projection = project_c1_grid_state(preview["base_values"], worksheet["decisions"])
    if projection != worksheet["projection"]:
        raise ValueError("served wire source projection changed")
    completed = datetime.fromisoformat(document["observed_at"])
    queried = datetime.fromisoformat(contract["query_at"])
    admitted = datetime.fromisoformat(preview["captured_at"])
    expires = datetime.fromisoformat(worksheet["expires_at"])
    if (
        any(t.tzinfo is None for t in (completed, queried, admitted, expires))
        or not admitted <= completed <= queried < expires
    ):
        raise ValueError("served wire source authority clock invalid")
    duration = (
        timedelta(minutes=8 + 4 * ((len(worksheet["decisions"]) + 11) // 12))
        if worksheet["schema"].endswith("-v3")
        else timedelta(minutes=6)
    )
    if (worksheet["schema"].endswith("-v3") and expires != admitted + duration) or expires > admitted + duration:
        raise ValueError("served wire source authority duration changed")
    observations = document["observed_components"]
    if set(observations) != set(projection["proposed_values"]):
        raise ValueError("served wire original complete48 projection observation missing")
    for name, desired_value in projection["proposed_values"].items():
        observed_value = observations[name]["value"]
        if isinstance(desired_value, bool):
            matches = type(observed_value) is bool and observed_value == desired_value
        else:
            matches = not isinstance(observed_value, bool) and F(observed_value) == F(desired_value)
        if not matches:
            raise ValueError("served wire original48 does not match source projection: " + name)
    inputs = preview["base_inputs"]
    if inputs["anchor_origin"] != "crop_band_anchors" or inputs["band_source"] != "anchors":
        raise ValueError("served wire requires original DB anchor policy")
    desired = selected_house_anchors(contract["anchor_rows"], contract["season"])
    if desired != {name: inputs["crop_anchors"][name] for name in ANCHOR_PARAMS}:
        raise ValueError("served wire current desired anchor policy changed")
    marker = contract["sample_epoch_callback"]
    if set(marker) != {"slug", "value", "observed_at"} or marker["slug"] != "consumed_band_sample_epoch":
        raise ValueError("served wire sample requires original text clock callback")
    marker_time = datetime.fromisoformat(marker["observed_at"])
    if (
        marker_time.tzinfo is None
        or not admitted <= marker_time <= completed
        or not 0 <= (completed - marker_time).total_seconds() <= 30
    ):
        raise ValueError("served wire sample clock callback not original/current")
    if document["band_source"]["value"] != "onchip_curve":
        raise ValueError("served wire contract requires consumed onchip branch")
    raw = marker["value"]
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal() or not 0 < int(raw) <= 0xFFFFFFFF:
        raise ValueError("served wire sample clock is invalid")
    from datetime import UTC

    sample = datetime.fromtimestamp(int(raw), UTC)
    if not admitted <= sample <= completed or not 0 <= (completed - sample).total_seconds() <= 30:
        raise ValueError("served wire sample outside original authority epoch")
    result = resolve_served_wire(desired, sample, projection["proposed_values"]["night_vpd_bias_kpa"])
    callbacks = contract["anchor_callbacks"]
    if set(callbacks) != set(ANCHOR_PARAMS):
        raise ValueError("served wire requires all24 original anchor callbacks")
    for name, row in callbacks.items():
        slug = REGISTRY[name].cfg_readback_object_id
        if set(row) != {"slug", "value", "observed_at"} or row["slug"] != slug:
            raise ValueError("served wire anchor callback route changed: " + name)
        moment = datetime.fromisoformat(row["observed_at"])
        if (
            moment.tzinfo is None
            or not admitted <= moment <= completed
            or not 0 <= (completed - moment).total_seconds() <= 60
        ):
            raise ValueError("served wire anchor callback not original/fresh: " + name)
        if isinstance(row["value"], bool) or F(row["value"]) != F(result["rounded_anchors"][name]):
            raise ValueError("served wire delivered anchor differs from desired: " + name)
        entities = [entity for entity in document["entities"] if entity["object_id"] == slug]
        unit = "°F" if name.startswith("band_temp_") else "kPa"
        if (
            len(entities) != 1
            or entities[0]["entity_type"] != "sensor"
            or entities[0]["disabled_by_default"]
            or entities[0]["unit"] != unit
        ):
            raise ValueError("served wire anchor metadata changed: " + name)
    if set(contract["ideal_sql"]) != set(SERIES) or set(contract["sql_functions"]) != set(SQL_FUNCTIONS):
        raise ValueError("served wire ideal SQL lineage incomplete")
    if (
        set(contract["database"]) != {"name", "server_version_num"}
        or contract["database"]["name"] != "verdify"
        or not str(contract["database"]["server_version_num"]).startswith("16")
    ):
        raise ValueError("served wire owning database contract changed")
    for name, definition in contract["sql_functions"].items():
        if (
            not isinstance(definition, str)
            or hashlib.sha256(definition.encode()).hexdigest() != IDEAL_SQL_FUNCTION_SHA256[name]
        ):
            raise ValueError("served wire owning ideal SQL function changed: " + name)
    if any(not isinstance(v, str) or not v.strip() for v in contract["sql_functions"].values()):
        raise ValueError("served wire ideal SQL function definition missing")
    if any(isinstance(v, bool) or not math.isfinite(float(v)) for v in contract["ideal_sql"].values()):
        raise ValueError("served wire ideal SQL tuple invalid")
    revision = wire_resolver_revision(contract)
    if document["revisions"]["crop_band_resolver_revision"] != revision:
        raise ValueError("served wire complete resolver provenance hash changed")
    for row in document["band_layers"]:
        series = row["series"]
        if (
            series not in SERIES
            or row["served"]["source"] != "independent_desired_anchor_device_wire_projection"
            or row["served"]["as_of"] != sample.isoformat()
        ):
            raise ValueError("served wire layer provenance changed")
        if isinstance(row["served"]["value"], bool) or F(row["served"]["value"]) != F(result["values"][series]):
            raise ValueError("served wire declared value is not independent expected projection")
    return {
        "contract": CONTRACT_SCHEMA,
        "resolver_revision": revision,
        "complete_evidence_sha256": content_digest(contract),
        "ideal_sql": contract["ideal_sql"],
        "served_wire": result["values"],
        "clock": result["clock"],
        "ideal_sql_equals_device_wire": all(
            F(contract["ideal_sql"][name]) == F(result["values"][name]) for name in SERIES
        ),
        "global_424_resolved": False,
    }
