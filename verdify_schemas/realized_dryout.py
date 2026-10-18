"""Bounded selected-night SQL evidence for the ordinary Iris read surface.

This does not recompute scientific verdicts or turn observed association into
causal efficacy. Every original getter field survives the typed projection.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

EPISODE_LIMIT = 500
RESPONSE_BYTE_LIMIT = 65_536
READER_SQL = """
SELECT * FROM public.fn_realized_solar_night_dryout($1::date, $1::date, $2::text)
ORDER BY episode_started_at, episode_id
LIMIT 501
"""


class RealizedDryoutEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    night_date: date
    episode_id: int
    episode_started_at: AwareDatetime
    episode_ended_at: AwareDatetime
    duration_min: float | None
    admission_status: str | None
    admission_reason: str | None
    block_reason: str | None
    stop_reason: str | None
    sample_minutes: int | None
    climate_coverage_pct: float | None
    max_climate_gap_s: int | None
    below_served_low_minutes: int | None
    action_evidence_minutes: int | None
    hold_admitted_minutes: int | None
    action_coverage_pct: float | None
    indoor_ah_avg_g_m3: float | None
    outdoor_ah_avg_g_m3: float | None
    ah_advantage_avg_g_m3: float | None
    ah_advantage_min_g_m3: float | None
    indoor_ah_before_g_m3: float | None
    indoor_ah_after_10_20m_g_m3: float | None
    observed_indoor_ah_delta_10_20m_g_m3: float | None
    served_temp_floor_f: float | None
    min_temp_f: float | None
    min_temp_floor_margin_f: float | None
    vpd_before_kpa: float | None
    vpd_after_10_20m_kpa: float | None
    observed_vpd_delta_10_20m_kpa: float | None
    observed_temp_delta_10_20m_f: float | None
    vent_duty_pct: float | None
    fan_duty_pct: float | None
    heat1_duty_pct: float | None
    heat2_duty_pct: float | None
    wet_relay_duty_pct: float | None
    response_wet_relay_minutes: int | None
    response_sample_minutes: int | None
    response_max_gap_s: int | None
    response_min_temp_floor_margin_f: float | None
    daytime_dry_action_samples: int | None
    daytime_hold_admission_samples: int | None
    safety_gate_status: str | None
    gate_violations: list[str]
    confound_reasons: list[str]
    evidence_status: str | None
    dryout_disposition: str | None


class RealizedDryoutSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    semantics: str
    episodes: list[RealizedDryoutEpisode] = Field(max_length=EPISODE_LIMIT)
    dispositions: dict[str, int]
    daytime_dry_action_samples: int | None
    daytime_hold_admission_samples: int | None
    safety_gate_status: Literal["pending", "fail", "pass", "incomplete"]


def project_realized_solar_night_dryout(rows):
    """Shared admin/Iris projection; safety and outcome validity remain separate."""
    return {
        "semantics": (
            "Measured solar-night demand and relay truth; projected planner "
            "intent is never labeled as realized outcome."
        ),
        "episodes": rows,
        "dispositions": {
            disposition: sum(row["dryout_disposition"] == disposition for row in rows)
            for disposition in ("effective", "ineffective", "blocked", "insufficient_evidence")
        },
        # Adjacent-day counts repeat per episode. Empty results cannot establish
        # measured zero daytime admission or complete daylight coverage.
        "daytime_dry_action_samples": max(
            (row["daytime_dry_action_samples"] for row in rows if row["daytime_dry_action_samples"] is not None),
            default=None,
        ),
        "daytime_hold_admission_samples": max(
            (
                row["daytime_hold_admission_samples"]
                for row in rows
                if row["daytime_hold_admission_samples"] is not None
            ),
            default=None,
        ),
        "safety_gate_status": (
            "pending"
            if not rows
            else "fail"
            if any(row["safety_gate_status"] == "fail" for row in rows)
            else "pass"
            if all(row["safety_gate_status"] == "pass" for row in rows)
            else "incomplete"
        ),
    }


class RealizedDryoutEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reader_contract_version: Literal[1] = 1
    availability: Literal["available", "no_episodes", "unavailable"] = "unavailable"
    unavailable_reason: (
        Literal[
            "not_requested",
            "explicit_night_date_required",
            "db_statement_timeout",
            "reader_unavailable",
            "invalid_evidence",
            "episode_limit_exceeded",
            "response_limit_exceeded",
        ]
        | None
    ) = "not_requested"
    night_date: date | None = None
    greenhouse_id: Literal["vallery"] = "vallery"
    query_budget_ms: int | None = None
    episode_limit: Literal[500] = EPISODE_LIMIT
    response_byte_limit: Literal[65536] = RESPONSE_BYTE_LIMIT
    source: Literal["public.fn_realized_solar_night_dryout(date,date,text)"] = (
        "public.fn_realized_solar_night_dryout(date,date,text)"
    )
    scope: Literal["selected_solar_night_and_adjacent_day_diagnostics"] = (
        "selected_solar_night_and_adjacent_day_diagnostics"
    )
    limitations: tuple[str, ...] = (
        "Safety pass is not effective dry-out, complete evidence or causal benefit.",
        "No episodes is not measured zero daytime admission or proof of no demand.",
        "Repeated daytime counts do not certify complete daylight coverage.",
        "Current helper fallback and source-coverage limits remain those of the installed getter.",
    )
    summary: RealizedDryoutSummary | None = None


async def read_realized_dryout_evidence(conn, day, query_budget_ms):
    """One bounded, read-only statement; no cache, retry or alternate identity."""
    import asyncpg

    budget = min(int(query_budget_ms), 15_000)
    if budget <= 0:
        raise ValueError("positive query budget required")
    scope = {"night_date": day, "query_budget_ms": budget}
    try:
        async with conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL statement_timeout = '{budget}ms'")
            rows = await conn.fetch(READER_SQL, day, "vallery", timeout=budget / 1000 + 0.5)
        if len(rows) > EPISODE_LIMIT:
            return RealizedDryoutEvidence(**scope, unavailable_reason="episode_limit_exceeded")
        episodes = [RealizedDryoutEpisode.model_validate(dict(row)) for row in rows]
        if any(episode.night_date != day for episode in episodes):
            raise ValueError("selected night mismatch")
        result = RealizedDryoutEvidence(
            **scope,
            availability="available" if episodes else "no_episodes",
            unavailable_reason=None,
            summary=RealizedDryoutSummary.model_validate(
                project_realized_solar_night_dryout([episode.model_dump() for episode in episodes])
            ),
        )
        if len(result.model_dump_json().encode("utf-8")) > RESPONSE_BYTE_LIMIT:
            return RealizedDryoutEvidence(**scope, unavailable_reason="response_limit_exceeded")
        return result
    except (asyncpg.QueryCanceledError, TimeoutError):
        reason = "db_statement_timeout"
    except (asyncpg.UndefinedFunctionError, asyncpg.UndefinedTableError, asyncpg.InsufficientPrivilegeError):
        reason = "reader_unavailable"
    except (ValidationError, ValueError, TypeError, KeyError, OverflowError):
        reason = "invalid_evidence"
    return RealizedDryoutEvidence(**scope, unavailable_reason=reason)
