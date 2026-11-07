"""Operator text for every setting (#1332, #1333): coverage, plain words, and code references that exist.

``verdify_schemas.setting_help`` is the one source the dashboard, MCP and the lab ai-tunables page read. A new
registry row with no text fails here, as does a ``refs`` item whose file or token is gone.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import pytest

from verdify_schemas.band_rows import HOUSE_SERIES
from verdify_schemas.setting_help import (
    HELP,
    band_in_force_help,
    band_in_force_sources,
    setting_help,
    setting_label,
    source_params,
)
from verdify_schemas.tunable_registry import REGISTRY, desired_write_rule

ROOT = Path(__file__).resolve().parents[2]
BAND_ZONES = ("house", "center", "south", "west", "east")
# Words the operator text never uses: internals, ticket and design-record ids, and setting keys (labels name them).
INTERNAL = re.compile(r"\b(MCP|ADR|TIER\d?|BC-\d+|sprint|dispatcher|registry|firmware)\b|#\d+", re.IGNORECASE)
SNAKE = re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b")
LIMITS = {"summary": 120, "effect": 420, "raise_": 260, "lower": 260, "caution": 260}
GUARDRAILS = {"safety_min", "safety_max", "safety_vpd_min", "safety_vpd_max"}


@cache
def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_every_registry_row_has_a_summary_and_an_effect():
    assert list(HELP) == list(REGISTRY), sorted(set(REGISTRY) ^ set(HELP))
    for name, entry in HELP.items():
        assert entry.summary.strip() and entry.effect.strip(), name


def test_every_writable_row_says_what_raising_and_lowering_it_does():
    for name, entry in HELP.items():
        if desired_write_rule(name)[0]:
            assert entry.raise_ and entry.lower, name


def test_the_text_is_short_plain_and_names_no_internals():
    for name, entry in HELP.items():
        for field, limit in LIMITS.items():
            value = getattr(entry, field)
            if value is None:
                continue
            assert len(value) <= limit, (name, field, len(value))
            assert not INTERNAL.search(value), (name, field, INTERNAL.search(value).group(0))
            assert not SNAKE.search(value), (name, field, SNAKE.search(value).group(0))


def test_every_ref_points_at_a_file_and_token_that_exist():
    for name, entry in HELP.items():
        assert entry.refs, f"{name}: cite the code that shows the effect"
        for ref in entry.refs:
            path, _, token = ref.partition("::")
            assert (ROOT / path).is_file(), (name, ref)
            if token:
                assert token in _text(path), (name, ref)


def test_no_label_is_the_raw_setting_name():
    for name in REGISTRY:
        assert setting_label(name) != name, name


def test_the_guardrails_are_marked_and_state_the_controller_rule():
    marked = {name for name, entry in HELP.items() if entry.guardrail}
    assert marked == GUARDRAILS
    for name in GUARDRAILS:
        assert "guardrail" in HELP[name].summary.lower(), name
        assert HELP[name].caution, name
    assert REGISTRY["safety_min"].default == 40.0 and REGISTRY["safety_max"].default == 100.0


def test_the_served_text_is_the_entry_without_its_refs():
    served = setting_help("mister_engage_kpa")
    entry = HELP["mister_engage_kpa"]
    assert served is not None
    body = served.model_dump(mode="json")
    assert set(body) == {"summary", "effect", "raise", "lower", "caution", "guardrail", "reference_url"}
    assert (body["summary"], body["raise"], body["lower"]) == (entry.summary, entry.raise_, entry.lower)
    assert body["reference_url"].endswith("/reference/ai-tunables/#mister_engage_kpa")
    assert setting_help("not_a_setting") is None


@pytest.mark.parametrize("zone", BAND_ZONES)
@pytest.mark.parametrize("role", HOUSE_SERIES)
def test_band_in_force_rows_say_they_are_computed_and_name_writable_sources(zone, role):
    text = band_in_force_help(zone, role)
    assert text.summary and "Computed" in text.effect and text.raise_ is None and text.lower is None
    assert not SNAKE.search(text.summary + text.effect)
    sources = band_in_force_sources(zone, role)
    assert sources and all(name in REGISTRY and desired_write_rule(name)[0] for name in sources), sources


def test_house_temp_high_comes_from_the_house_anchors_and_a_zone_vpd_edge_adds_its_width():
    assert band_in_force_sources("house", "temp_high") == tuple(
        f"band_temp_high_{a}" for a in ("sr", "sm", "ss", "mid")
    )
    assert band_in_force_sources("west", "temp_low") == band_in_force_sources("house", "temp_low")
    assert band_in_force_sources("south", "vpd_high")[-1] == "zone_vpd_width_above_south"


def test_derived_registry_rows_name_writable_sources():
    for name in REGISTRY:
        for source in source_params(name):
            assert source in REGISTRY and desired_write_rule(source)[0], (name, source)
    assert source_params("activity_start_hour")


def test_no_setting_text_names_a_non_public_crop():
    """The lab ai-tunables page renders this text publicly; the public-output guard refuses excluded crops."""
    from verdify_public.output_policy import PUBLIC_CROP_EXCLUDE_SLUGS

    texts = [repr(entry) for entry in HELP.values()]
    texts += [repr(band_in_force_help(zone, role)) for zone in BAND_ZONES for role in HOUSE_SERIES]
    found = sorted({slug for text in texts for slug in PUBLIC_CROP_EXCLUDE_SLUGS if slug in text.casefold()})
    assert not found, f"setting text names non-public crops: {found}"
