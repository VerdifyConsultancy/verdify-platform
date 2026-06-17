"""L3 #345 AC5 — green-band compliance must distinguish a CONTROLLER miss from a
PHYSICALLY-UNACHIEVABLE miss.

The live decomposition is decision #2 of docs/design/band-compliance-architecture.md
(§6.2) and is implemented in SQL as the feasibility classifier inside
`fn_zone_band_grade` (db/migrations/146-compliance-rearchitecture.sql:259-278).
Because that rule lives in SQL, running it end-to-end needs a live DB (DB-gated).
The OFFLINE-provable slice this test pins:

  1. A Python reference of the exact §6.2 rule, asserted on synthetic miss rows
     so the decomposition semantics are nailed (a hot-miss with the cooling stack
     saturated against >= ambient is UNACHIEVABLE; a hot-miss with idle cooling
     authority is the CONTROLLER's fault; the "tightened" VPD-high rule does not
     credit vent-while-temp-OK as unachievable — that was gameable).
  2. A guard that the migration-146 SQL still carries the same predicates, so a
     future migration cannot silently re-conflate weather with controller fault
     (the same cross-impl-alignment discipline the band-curve goldens use).

If §6.2 changes, BOTH the reference here and the SQL must change together.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MIG_146 = REPO / "db" / "migrations" / "146-compliance-rearchitecture.sql"


def classify_feasibility(
    *,
    g_temp: float | None,
    g_vpd: float | None,
    reading_temp: float,
    reading_vpd: float,
    temp_low: float,
    temp_high: float,
    vpd_low: float,
    vpd_high: float,
    served_temp_high: float,
    outdoor_temp_f: float | None,
    have_relay: bool,
    vent_on: bool = False,
    fan1_on: bool = False,
    fan2_on: bool = False,
    heat1_on: bool = False,
    heat2_on: bool = False,
    fog_on: bool = False,
    mister_on: bool = False,
) -> str:
    """Faithful Python mirror of fn_zone_band_grade's feasibility CASE
    (db/migrations/146:257-278). NULL-safe comparisons match SQL three-valued
    logic: a comparison against a NULL outdoor reading is falsy (-> ELSE branch),
    exactly as `g.outdoor_temp_f >= ...` evaluates to NULL and skips the WHEN."""

    def ge(a: float | None, b: float | None) -> bool:
        return a is not None and b is not None and a >= b

    if not have_relay:
        return "feasibility_unknown"

    # HOT miss: graded temp credit < 1 on the hot side.
    if g_temp is not None and g_temp < 1 and reading_temp > temp_high:
        if vent_on and ge(outdoor_temp_f, served_temp_high):
            return "unachievable"  # can't beat ambient with an exhaust-only box
        if vent_on and fan1_on and fan2_on and ge(outdoor_temp_f, reading_temp):
            return "unachievable"  # full cooling stack, 2nd fan futile
        return "controller"  # a cooling stage was idle while outdoor < indoor

    # COLD miss.
    if g_temp is not None and g_temp < 1 and reading_temp < temp_low:
        if heat1_on and heat2_on:
            return "unachievable"  # both heaters maxed
        return "controller"

    # VPD-HIGH (too dry).
    if g_vpd is not None and g_vpd < 1 and reading_vpd > vpd_high:
        if vent_on and (reading_temp > temp_high or ge(outdoor_temp_f, served_temp_high)):
            return "unachievable"  # venting genuinely forced by heat (tightened rule)
        if fog_on or mister_on:
            return "unachievable"  # humidifying at full authority
        return "controller"  # vent-while-temp-OK is NOT unachievable (anti-gaming)

    # VPD-LOW (too humid) — no passive dehumidification path; always controller.
    if g_vpd is not None and g_vpd < 1 and reading_vpd < vpd_low:
        return "controller"

    return "none"  # in band, no miss


# Shared band geometry for the synthetic rows.
BAND = dict(temp_low=60.0, temp_high=85.0, vpd_low=0.6, vpd_high=1.5, served_temp_high=86.0)


def test_hot_miss_unachievable_primary_rail_cannot_beat_ambient():
    f = classify_feasibility(
        g_temp=0.4,
        g_vpd=1.0,
        reading_temp=90.0,
        reading_vpd=1.0,
        outdoor_temp_f=88.0,
        have_relay=True,
        vent_on=True,
        **BAND,
    )
    assert f == "unachievable"


def test_hot_miss_unachievable_full_stack_second_fan_futile():
    f = classify_feasibility(
        g_temp=0.4,
        g_vpd=1.0,
        reading_temp=90.0,
        reading_vpd=1.0,
        outdoor_temp_f=91.0,
        have_relay=True,
        vent_on=True,
        fan1_on=True,
        fan2_on=True,
        **BAND,
    )
    assert f == "unachievable"


def test_hot_miss_controller_when_cooling_idle_and_outdoor_cooler():
    # outdoor (80) < served_temp_high (86) and < indoor: there WAS cooling headroom.
    f = classify_feasibility(
        g_temp=0.4,
        g_vpd=1.0,
        reading_temp=90.0,
        reading_vpd=1.0,
        outdoor_temp_f=80.0,
        have_relay=True,
        vent_on=False,
        **BAND,
    )
    assert f == "controller"


def test_cold_miss_unachievable_when_both_heaters_maxed_else_controller():
    maxed = classify_feasibility(
        g_temp=0.2,
        g_vpd=1.0,
        reading_temp=45.0,
        reading_vpd=1.0,
        outdoor_temp_f=20.0,
        have_relay=True,
        heat1_on=True,
        heat2_on=True,
        **BAND,
    )
    assert maxed == "unachievable"
    idle = classify_feasibility(
        g_temp=0.2,
        g_vpd=1.0,
        reading_temp=45.0,
        reading_vpd=1.0,
        outdoor_temp_f=20.0,
        have_relay=True,
        heat1_on=True,
        heat2_on=False,
        **BAND,
    )
    assert idle == "controller"


def test_vpd_high_unachievable_when_vent_forced_or_humidifying():
    forced_vent = classify_feasibility(
        g_temp=1.0,
        g_vpd=0.3,
        reading_temp=90.0,
        reading_vpd=2.6,  # temp > temp_high forces vent
        outdoor_temp_f=70.0,
        have_relay=True,
        vent_on=True,
        **BAND,
    )
    assert forced_vent == "unachievable"
    humidifying = classify_feasibility(
        g_temp=1.0,
        g_vpd=0.3,
        reading_temp=80.0,
        reading_vpd=2.6,
        outdoor_temp_f=70.0,
        have_relay=True,
        fog_on=True,
        **BAND,
    )
    assert humidifying == "unachievable"


def test_vpd_high_controller_anti_gaming_vent_while_temp_ok():
    # Tightened §6.2 rule: venting while temp is already in band is NOT credited
    # as unachievable (a controller could otherwise harvest free credit by venting).
    f = classify_feasibility(
        g_temp=1.0,
        g_vpd=0.3,
        reading_temp=80.0,
        reading_vpd=2.6,  # temp in band
        outdoor_temp_f=70.0,
        have_relay=True,
        vent_on=True,
        **BAND,
    )
    assert f == "controller"


def test_vpd_low_is_always_controller():
    f = classify_feasibility(
        g_temp=1.0, g_vpd=0.3, reading_temp=75.0, reading_vpd=0.2, outdoor_temp_f=60.0, have_relay=True, **BAND
    )
    assert f == "controller"


def test_feasibility_unknown_before_relay_coverage_and_none_in_band():
    unknown = classify_feasibility(
        g_temp=0.4,
        g_vpd=1.0,
        reading_temp=90.0,
        reading_vpd=1.0,
        outdoor_temp_f=88.0,
        have_relay=False,
        vent_on=True,
        **BAND,
    )
    assert unknown == "feasibility_unknown"
    none = classify_feasibility(
        g_temp=1.0, g_vpd=1.0, reading_temp=75.0, reading_vpd=1.0, outdoor_temp_f=70.0, have_relay=True, **BAND
    )
    assert none == "none"


def test_null_outdoor_does_not_credit_unachievable():
    # SQL: `outdoor_temp_f >= served_temp_high` is NULL when outdoor is NULL ->
    # the WHEN is skipped -> controller. The Python ge() mirrors that.
    f = classify_feasibility(
        g_temp=0.4,
        g_vpd=1.0,
        reading_temp=90.0,
        reading_vpd=1.0,
        outdoor_temp_f=None,
        have_relay=True,
        vent_on=True,
        **BAND,
    )
    assert f == "controller"


def test_migration_146_sql_still_carries_the_documented_rule():
    """Cross-impl guard: the SQL feasibility classifier must keep the §6.2
    predicates so it cannot silently diverge from the reference above."""
    assert MIG_146.exists(), f"missing {MIG_146}"
    sql = MIG_146.read_text(encoding="utf-8")
    required = [
        "feasibility classifier",  # the labelled block
        "'unachievable'",
        "'controller'",
        "'feasibility_unknown'",
        "g.outdoor_temp_f >= g.served_temp_high",  # HOT primary rail
        "g.outdoor_temp_f >= g.reading_temp",  # HOT full-stack rail
        "g.heat1_on",  # COLD rail
        "g.heat2_on",
        "g.reading_temp > g.temp_high",  # VPD-high tightened rail
    ]
    missing = [needle for needle in required if needle not in sql]
    assert not missing, (
        "migration 146 feasibility classifier drifted from the documented §6.2 "
        f"rule — missing predicates: {missing}. Update this test AND the SQL together."
    )
