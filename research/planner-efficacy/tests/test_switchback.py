from __future__ import annotations

import hashlib
import hmac
import struct
import sys
import unicodedata
import uuid
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest

MODULE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(MODULE_DIR))

from switchback import analysis, randomization

STUDY_ID = "verdify-gh1-switchback-2026"
STUDY_ID_NFC = "stud\u00e9-nfc"  # precomposed e-acute
STUDY_ID_NFD = "stude\u0301-nfc"  # decomposed e + combining acute
BEACON = bytes(range(32))
BEACON_2 = hashlib.sha256(b"beacon-two").digest()
SECRET = bytes.fromhex("00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff")
SECRET_2 = hashlib.sha256(b"secret-two").digest()
NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def _nfc(study_id: str) -> bytes:
    return unicodedata.normalize("NFC", study_id).encode("utf-8")


# --- Section 8.3 byte-exact derivations -------------------------------------
# Each derivation is double-implemented: the expected digest is recomputed
# inline from the spec strings AND guarded by hardcoded hex so neither copy of
# the construction can drift silently.


@pytest.mark.parametrize(
    ("study_id", "beacon", "j", "digest_hex", "expected_order"),
    [
        (STUDY_ID, BEACON, 0, "f11a71f161acddbb504ba896219dde9390bde74dc85754de4050cd3060fccc0e", "XY"),
        (STUDY_ID, BEACON, 7, "3e4b872789d339c204f8581752d9d9bedcfafc65c31dce3a05dd73fe7da34549", "YX"),
        (STUDY_ID_NFC, BEACON_2, 14, "483b3a4c045b1e28520b91b7e50a839584b5ad3c3260041726755af44e842570", "XY"),
    ],
)
def test_pair_order_byte_exact(study_id: str, beacon: bytes, j: int, digest_hex: str, expected_order: str) -> None:
    message = b"verdify-switchback-order-v1" + b"\x00" + _nfc(study_id) + b"\x00" + struct.pack(">I", j)
    digest = hmac.new(beacon, message, hashlib.sha256).digest()
    assert digest.hex() == digest_hex
    assert randomization.pair_order(beacon, study_id, j) == ("XY" if (digest[31] & 0x01) == 0 else "YX")
    assert randomization.pair_order(beacon, study_id, j) == expected_order


@pytest.mark.parametrize(
    ("study_id", "secret", "digest_hex", "expected_map"),
    [
        (STUDY_ID, SECRET, "ab0fe695f0c6e4012bc778c546eecd92e777a277a15f20a7948485f00d2b7d4c", {"X": "A", "Y": "B"}),
        (
            STUDY_ID_NFC,
            SECRET_2,
            "91592feeced2e4b3ccfd59597070f06f34e961b20943f63e94edfff2bf20c631",
            {"X": "B", "Y": "A"},
        ),
    ],
)
def test_arm_mapping_byte_exact(study_id: str, secret: bytes, digest_hex: str, expected_map: dict) -> None:
    message = b"verdify-switchback-arm-map-v1" + b"\x00" + _nfc(study_id)
    digest = hmac.new(secret, message, hashlib.sha256).digest()
    assert digest.hex() == digest_hex
    assert randomization.arm_mapping(secret, study_id) == expected_map


@pytest.mark.parametrize(
    ("study_id", "secret", "commitment_hex"),
    [
        (STUDY_ID, SECRET, "86b13f5ceb77eb4e4353d7e39cc248171d69d406e652bdbbaf6f1254693be06e"),
        (STUDY_ID_NFC, SECRET_2, "5606a373320d42c0a1f7114b0180f5954fe76ef065a67dee4b5f9a45230a5771"),
    ],
)
def test_mapping_commitment_byte_exact(study_id: str, secret: bytes, commitment_hex: str) -> None:
    preimage = b"verdify-switchback-map-commit-v1" + b"\x00" + _nfc(study_id) + b"\x00" + secret
    assert hashlib.sha256(preimage).hexdigest() == commitment_hex
    assert randomization.mapping_commitment(study_id, secret).hex() == commitment_hex


def test_nfc_normalization_is_applied() -> None:
    assert randomization.pair_order(BEACON_2, STUDY_ID_NFD, 14) == randomization.pair_order(BEACON_2, STUDY_ID_NFC, 14)
    assert randomization.mapping_commitment(STUDY_ID_NFD, SECRET_2) == randomization.mapping_commitment(
        STUDY_ID_NFC, SECRET_2
    )
    assert randomization.arm_mapping(SECRET_2, STUDY_ID_NFD) == randomization.arm_mapping(SECRET_2, STUDY_ID_NFC)


def test_mapping_secret_length_is_enforced() -> None:
    with pytest.raises(ValueError):
        randomization.mapping_commitment(STUDY_ID, b"short")
    with pytest.raises(ValueError):
        randomization.arm_mapping(SECRET + b"\x00", STUDY_ID)


@pytest.mark.parametrize(
    ("study_id", "local_date", "expected"),
    [
        (STUDY_ID, "2026-09-01", "3e544ad0-07a7-5897-933e-2f4e289e2d68"),
        (STUDY_ID_NFC, "2026-09-15", "913d2fbb-d88d-5b5a-9d84-12e4a9d6064f"),
    ],
)
def test_assignment_uuid_byte_exact(study_id: str, local_date: str, expected: str) -> None:
    name = _nfc(study_id) + b"\x00" + local_date.encode("ascii")
    raw = bytearray(hashlib.sha1(NAMESPACE.bytes + name, usedforsecurity=False).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x50
    raw[8] = (raw[8] & 0x3F) | 0x80
    result = randomization.assignment_uuid(NAMESPACE, study_id, local_date)
    assert result == uuid.UUID(bytes=bytes(raw))
    assert str(result) == expected
    assert result.version == 5
    assert result.variant == uuid.RFC_4122


def test_assignment_uuid_rejects_bad_date() -> None:
    with pytest.raises(ValueError):
        randomization.assignment_uuid(NAMESPACE, STUDY_ID, "2026/09/01")


# --- RFC 8785 canonicalization ----------------------------------------------


def test_rfc8785_sorting_escapes_and_whitespace() -> None:
    value = {"b": 2, "a": "x", "nested": {"z": [1, "two"], "é": "acute"}}
    assert randomization.rfc8785_canonicalize(value) == '{"a":"x","b":2,"nested":{"z":[1,"two"],"é":"acute"}}'
    assert randomization.rfc8785_canonicalize({"s": 'a\tb\nc\x01"\\'}) == '{"s":"a\\tb\\nc\\u0001\\"\\\\"}'


def test_rfc8785_key_sort_uses_utf16_code_units() -> None:
    # U+1D306 (surrogate pair, leading unit 0xD834) sorts before U+FB33 in
    # UTF-16 order would be false; per RFC 8785 appendix, "דּ" < "\U0001d306"
    # is FALSE in code-point order but TRUE... verify against explicit encoding.
    keys = ["\U0001d306", "דּ", "z"]
    expected = sorted(keys, key=lambda k: k.encode("utf-16-be"))
    out = randomization.rfc8785_canonicalize({k: 1 for k in keys})
    positions = {k: out.index(k) for k in keys}
    assert sorted(keys, key=lambda k: positions[k]) == expected


def test_rfc8785_rejects_non_int_str_values() -> None:
    for bad in ({"a": 1.5}, {"a": True}, {"a": None}, {1: "x"}):
        with pytest.raises((TypeError, ValueError)):
            randomization.rfc8785_canonicalize(bad)
    with pytest.raises(ValueError):
        randomization.rfc8785_canonicalize({"a": 2**53 + 1})


# --- Blinded schedule --------------------------------------------------------


def _schedule() -> dict:
    return randomization.blinded_schedule(
        STUDY_ID,
        "2026-09-01",
        beacon_bytes=BEACON,
        namespace_uuid=NAMESPACE,
    )


def test_blinded_schedule_structure_and_determinism() -> None:
    one, two = _schedule(), _schedule()
    assert one == two
    blinded = one["blinded_assignment"]
    days = blinded["assignments"]
    assert len(days) == 30
    assert [d["pair_id"] for d in days] == [j for j in range(15) for _ in (1, 2)]
    # Independently derived pair orders for this beacon/study.
    expected_orders = ["XY", "YX", "XY", "XY", "YX", "XY", "YX", "YX", "XY", "XY", "YX", "XY", "XY", "XY", "XY"]
    got_orders = ["".join(d["blinded_label"] for d in days[2 * j : 2 * j + 2]) for j in range(15)]
    assert got_orders == expected_orders
    # Half-open UTC ranges chain: each day ends where the next begins; Denver
    # September is UTC-6, so local midnight is 06:00:00Z.
    assert days[0]["utc_start"] == "2026-09-01T06:00:00Z"
    assert days[0]["utc_end"] == "2026-09-02T06:00:00Z"
    for previous, current in pairwise(days):
        assert previous["utc_end"] == current["utc_start"]
    assert days[0]["assignment_uuid"] == "3e544ad0-07a7-5897-933e-2f4e289e2d68"
    # Hash matches an inline recomputation of SHA256(UTF8(RFC8785(...))).
    canonical = randomization.rfc8785_canonicalize(blinded)
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == one["schedule_hash_sha256"]


def test_blinded_schedule_rejects_dst_crossing_window() -> None:
    # 2026-11-01 is the US fall-back transition in America/Denver.
    with pytest.raises(ValueError, match="UTC-offset transition"):
        randomization.blinded_schedule(STUDY_ID, "2026-10-15", beacon_bytes=BEACON, namespace_uuid=NAMESPACE)
    # Spring-forward 2026-03-08 likewise.
    with pytest.raises(ValueError, match="UTC-offset transition"):
        randomization.blinded_schedule(STUDY_ID, "2026-03-01", beacon_bytes=BEACON, namespace_uuid=NAMESPACE)


def test_resolve_schedule_round_trip() -> None:
    resolved = randomization.resolve_schedule(_schedule(), SECRET)
    assert resolved["arm_mapping"] == {"X": "A", "Y": "B"}
    assert resolved["mapping_commitment_sha256"] == "86b13f5ceb77eb4e4353d7e39cc248171d69d406e652bdbbaf6f1254693be06e"
    assert resolved["blinded_schedule_hash_sha256"] == _schedule()["schedule_hash_sha256"]
    for day in resolved["assignments"]:
        assert day["physical_arm"] == {"X": "A", "Y": "B"}[day["blinded_label"]]
    arms = [d["physical_arm"] for d in resolved["assignments"]]
    assert arms.count("A") == 15 and arms.count("B") == 15


# --- Section 8.9 treatment octets --------------------------------------------


def test_treatment_octet_goldens() -> None:
    assert randomization.randomized_treatment_bytes("X").hex() == "0158"
    assert randomization.randomized_treatment_bytes("Y").hex() == "0159"
    assert randomization.aa_treatment_bytes(0).hex() == "0300"
    assert randomization.aa_treatment_bytes(1).hex() == "0301"
    source = uuid.UUID("00000000-0000-0000-0000-000000000001")
    target = uuid.UUID("00000000-0000-0000-0000-000000000002")
    payload = randomization.qualification_treatment_bytes("analyzed", source, target, "night")
    assert payload.hex() == "0201" + "00" * 15 + "01" + "00" * 15 + "02" + "00"
    assert (
        randomization.qualification_treatment_bytes("identity_hold", source, target, "other_daylight")[:2].hex()
        == "0204"
    )
    assert randomization.qualification_treatment_bytes("positioning", source, target, "hot_bright_dry")[-1] == 0x01
    assert (
        randomization.qualification_treatment_bytes("baseline_recovery", source, target, "hot_bright_humid")[-1] == 0x02
    )
    with pytest.raises(ValueError):
        randomization.randomized_treatment_bytes("A")
    with pytest.raises(ValueError):
        randomization.aa_treatment_bytes(2)


# --- Frozen analyzer ---------------------------------------------------------


def _resolved_for_analysis() -> dict:
    return randomization.resolve_schedule(_schedule(), SECRET)


def _outcomes(resolved: dict, effects: dict[str, float], noise_sd: dict[str, float], seed: int = 7) -> dict:
    """Synthetic outcomes: arm A at a base level, arm B shifted by `effects`."""
    rng = np.random.default_rng(seed)
    base = {"vpd_distance_kpa": 0.10, "temp_distance_f": 0.51, "nine_device_minutes": 2000.0}
    rows: dict[str, dict[str, float]] = {}
    for day in resolved["assignments"]:
        values = {}
        for column, level in base.items():
            value = level + rng.normal(0.0, noise_sd[column])
            if day["physical_arm"] == "B":
                value += effects[column]
            values[column] = value
        rows[day["local_date"]] = values
    return rows


def test_analyzer_recovers_known_effect_and_advances() -> None:
    resolved = _resolved_for_analysis()
    effects = {"vpd_distance_kpa": -0.02, "temp_distance_f": -0.10, "nine_device_minutes": -400.0}
    noise = {"vpd_distance_kpa": 0.005, "temp_distance_f": 0.05, "nine_device_minutes": 40.0}
    report = analysis.analyze(resolved, _outcomes(resolved, effects, noise))
    assert report["decision"] == "advance"
    assert not report["influence_sensitive"]
    for spec in analysis.CO_PRIMARY_ENDPOINTS:
        endpoint = report["endpoints"][spec.name]
        primary = endpoint["primary"]
        assert primary["pairs"] == 15
        assert primary["t_critical"] == pytest.approx(2.144786688)
        assert primary["mean"] == pytest.approx(effects[spec.column], abs=4 * noise[spec.column])
        # Inline recomputation of the locked formula.
        d = np.asarray(endpoint["contrasts"])
        assert primary["upper_bound_97_5"] == pytest.approx(
            float(np.mean(d)) + 2.144786688 * float(np.std(d, ddof=1)) / np.sqrt(15)
        )
        assert primary["passes"] and primary["upper_bound_97_5"] < spec.boundary
        assert len(endpoint["leave_one_pair_out"]) == 15
        loo0 = endpoint["leave_one_pair_out"][0]
        rest = np.delete(d, 0)
        assert loo0["upper_bound_97_5"] == pytest.approx(
            float(np.mean(rest)) + 2.160368656 * float(np.std(rest, ddof=1)) / np.sqrt(14)
        )


def test_analyzer_flags_influence_sensitive_run_as_inconclusive() -> None:
    resolved = _resolved_for_analysis()
    effects = {"vpd_distance_kpa": 0.0, "temp_distance_f": 0.0, "nine_device_minutes": -5.0}
    noise = {"vpd_distance_kpa": 1e-4, "temp_distance_f": 1e-3, "nine_device_minutes": 1.0}
    outcomes = _outcomes(resolved, effects, noise)
    # One dominant adverse pair inflates both the mean and the sd: the full
    # 15-pair nine-device bound fails, but deleting that single pair flips the
    # classification to pass -> influence-sensitive, inconclusive (Section 8.4).
    b_day = next(d["local_date"] for d in resolved["assignments"] if d["pair_id"] == 3 and d["physical_arm"] == "B")
    outcomes[b_day]["nine_device_minutes"] += 40.0
    report = analysis.analyze(resolved, outcomes)
    endpoint = report["endpoints"]["nine_device_runtime"]
    assert not endpoint["primary"]["passes"]
    assert endpoint["leave_one_pair_out"][3]["passes"]
    assert endpoint["influence_sensitive"]
    assert report["decision"] == "inconclusive_influence_sensitive"


def test_analyzer_evidence_against_on_climate_harm() -> None:
    resolved = _resolved_for_analysis()
    effects = {"vpd_distance_kpa": 0.09, "temp_distance_f": 0.0, "nine_device_minutes": -300.0}
    noise = {"vpd_distance_kpa": 0.004, "temp_distance_f": 0.05, "nine_device_minutes": 30.0}
    report = analysis.analyze(resolved, _outcomes(resolved, effects, noise))
    vpd = report["endpoints"]["vpd_corridor_distance"]["primary"]
    assert not vpd["passes"]
    assert vpd["evidence_against"]  # lower bound above the +0.05 kPa margin
    assert report["decision"] == "evidence_against"


def test_analyzer_requires_outcome_complete_ledger() -> None:
    resolved = _resolved_for_analysis()
    outcomes = _outcomes(
        resolved,
        {"vpd_distance_kpa": 0.0, "temp_distance_f": 0.0, "nine_device_minutes": 0.0},
        {"vpd_distance_kpa": 0.01, "temp_distance_f": 0.05, "nine_device_minutes": 50.0},
    )
    outcomes.pop(resolved["assignments"][5]["local_date"])
    with pytest.raises(ValueError, match="integrity/feasibility"):
        analysis.analyze(resolved, outcomes)
    with pytest.raises(ValueError, match="unresolved arm"):
        blinded_only = dict(resolved)
        blinded_only["assignments"] = [{**d, "physical_arm": d["blinded_label"]} for d in resolved["assignments"]]
        analysis.pair_contrasts(blinded_only, outcomes)


def test_randomization_inversion_constant_effect_sanity() -> None:
    spec = analysis.CO_PRIMARY_ENDPOINTS[2]  # nine-device, boundary 0, step 10
    true_effect = -200.0
    contrasts = np.full(15, true_effect)
    result = analysis.randomization_inversion(contrasts, spec)
    # With a sharp constant effect and zero noise, tau = true effect cannot be
    # rejected (all sign assignments give statistic 0 at tau=true_effect), and
    # every tau meaningfully above it is rejected one-sided.
    assert result["upper_bound_97_5"] >= true_effect
    assert result["upper_bound_97_5"] < spec.boundary
    assert not result["grid_censored"]
    assert result["p_lower_at_boundary"] <= 2.0 / 2**15  # only the all-plus sign vector reaches the observed statistic
    # At tau equal to the sharp true effect, every sign assignment ties at 0,
    # so the exact p-value is 1 and tau = -200 is on the accepted grid.
    signs = analysis._sign_matrix(15)
    centered = contrasts - true_effect
    assert float(np.mean(signs @ centered <= 0.0 + 1e-9)) == 1.0
    assert result["upper_bound_97_5"] == pytest.approx(true_effect)


def test_randomization_inversion_upper_bound_tracks_t_bound() -> None:
    rng = np.random.default_rng(11)
    contrasts = rng.normal(-0.02, 0.01, size=15)
    spec = analysis.CO_PRIMARY_ENDPOINTS[0]  # vpd, boundary +0.05, step 0.005
    result = analysis.randomization_inversion(contrasts, spec)
    t_upper = float(np.mean(contrasts)) + 2.144786688 * float(np.std(contrasts, ddof=1)) / np.sqrt(15)
    assert result["upper_bound_97_5"] == pytest.approx(t_upper, abs=3 * spec.grid_step)
    assert result["upper_bound_97_5"] < spec.boundary


def test_load_outcomes_csv_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "outcomes.csv"
    path.write_text(
        "local_date,vpd_distance_kpa,temp_distance_f,nine_device_minutes\n"
        "2026-09-01,0.10,0.51,2000\n"
        "2026-09-02,0.08,0.44,1800\n"
    )
    rows = analysis.load_outcomes_csv(path)
    assert rows["2026-09-02"]["nine_device_minutes"] == 1800.0
    path.write_text("local_date,vpd_distance_kpa\n2026-09-01,0.1\n")
    with pytest.raises(ValueError, match="missing columns"):
        analysis.load_outcomes_csv(path)
