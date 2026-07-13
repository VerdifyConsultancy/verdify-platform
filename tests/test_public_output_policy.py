from __future__ import annotations

import base64

from verdify_public import output_policy as policy


def excluded_identifier() -> str:
    return next(iter(policy.PUBLIC_CROP_EXCLUDE_SLUGS))


def test_crop_identity_is_fail_closed_but_empty_positions_are_allowed():
    excluded = excluded_identifier()

    assert not policy.is_public_crop(None)
    assert not policy.is_public_crop("")
    assert not policy.is_public_crop(f"  {excluded.upper()}  ")
    assert policy.is_public_crop("canna")
    assert policy.is_public_crop_record(None, None, occupied=False)
    assert not policy.is_public_crop_record(None, "unknown crop", occupied=True)
    assert not policy.is_public_crop_record("canna", None, occupied=True)
    assert not policy.is_public_crop_record("canna", "  ", occupied=True)
    assert not policy.is_public_crop_record("canna", f"near {excluded} row", occupied=True)
    assert policy.is_public_crop_record("canna", "Canna lily", occupied=True)
    assert not policy.contains_non_public_crop_reference(f"{excluded}x")
    assert not policy.contains_non_public_crop_reference(f"x{excluded}")


def test_redactor_preserves_near_matches_and_redacts_nested_public_data():
    excluded = excluded_identifier()
    payload = {
        "name": f"{excluded.title()} crop",
        "notes": [f"inspect {excluded}-test", "canna and cannabinoid remain public"],
        f"{excluded}_metric": "hidden key",
        "count": 0,
    }

    redacted = policy.redact_public_data(payload)

    assert excluded not in str(redacted).casefold()
    assert policy.PUBLIC_CROP_REDACTION in redacted["name"]
    assert redacted["notes"][1] == "canna and cannabinoid remain public"
    assert all(excluded not in str(key).casefold() for key in redacted)
    assert redacted["count"] == 0


def test_shared_decoder_fails_closed_for_web_and_base64_encodings_without_echoing_identity():
    excluded = excluded_identifier()
    percent_encoded = "".join(f"%{byte:02X}" for byte in excluded.encode())
    html_encoded = "".join(f"&#x{ord(char):x};" for char in excluded)
    json_unicode = "".join(f"\\u{ord(char):04x}" for char in excluded)
    base64_encoded = base64.b64encode(excluded.encode()).decode()
    base64_url_encoded = base64.urlsafe_b64encode(excluded.encode()).decode().rstrip("=")
    double_base64 = base64.b64encode(base64_encoded.encode()).decode()
    encoded_values = [percent_encoded, html_encoded, json_unicode, base64_encoded, base64_url_encoded, double_base64]

    for encoded in encoded_values:
        assert policy.contains_non_public_crop_reference(f"before:{encoded}:after")
        assert not policy.is_public_crop(encoded)
        redacted = policy.redact_non_public_crop_references(f"before:{encoded}:after")
        assert redacted == policy.PUBLIC_CROP_REDACTION
        assert excluded not in redacted.casefold()
        assert encoded not in redacted

    across_window = "x" * (policy.PUBLIC_DECODE_WINDOW_CHARS - 5) + " " + percent_encoded
    assert policy.contains_non_public_crop_reference(across_window)


def test_shared_decoder_preserves_safe_encoded_near_matches():
    safe = base64.b64encode(b"canna lily").decode()

    assert not policy.contains_non_public_crop_reference(safe)
    assert policy.redact_non_public_crop_references(safe) == safe


def test_shared_decoder_limit_hits_are_fail_closed_and_non_reflective():
    oversized = "A" * (policy.PUBLIC_DECODE_MAX_BASE64_TOKEN_CHARS + 1)

    assert policy.decode_public_text(oversized).limit_hit
    assert policy.contains_non_public_crop_reference(oversized)
    assert policy.redact_non_public_crop_references(oversized) == policy.PUBLIC_CROP_REDACTION


def test_zone_sql_policy_uses_one_canonical_position_legacy_and_public_predicate():
    joins = policy.public_crop_zone_joins()
    predicate = policy.public_crop_zone_predicate("$1", "cc.slug", "c.name", 2, 3)

    assert "LEFT JOIN positions p" in joins
    assert "LEFT JOIN shelves sh" in joins
    assert "c.zone_id IS NULL" in joins
    assert "sh.zone_id IS NULL" in joins
    assert "lower(btrim(legacy_zone.slug)) = lower(btrim(c.zone))" in joins
    assert predicate.startswith("COALESCE(c.zone_id, sh.zone_id, legacy_zone.id) = $1")
    assert "cc.slug IS NOT NULL" in predicate
    assert "c.name IS NOT NULL" in predicate
