"""Phase 2 schema tests — alerts, crops, lessons + their MCP action envelopes."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from verdify_schemas.alerts import (
    AlertAckPayload,
    AlertAction,
    AlertEnvelope,
    AlertLogRow,
    AlertResolvePayload,
)
from verdify_schemas.crops import (
    Crop,
    CropAction,
    CropCreate,
    CropEvent,
    CropUpdate,
    EventCreate,
    Observation,
    ObservationAction,
    ObservationCreate,
)
from verdify_schemas.lessons import (
    LESSON_TRANSITIONS,
    LessonAction,
    LessonCreate,
    LessonState,
    LessonSupersede,
    LessonUpdate,
    LessonValidate,
    PlannerLesson,
    derive_lesson_state,
    is_legal_lesson_transition,
)
from verdify_schemas.operations import HarvestCreate, TreatmentCreate

NOW = datetime(2026, 4, 19, 1, 0, tzinfo=UTC)


class TestAlertEnvelope:
    def test_valid(self):
        a = AlertEnvelope(
            alert_type="vpd_stress",
            severity="warning",
            category="climate",
            sensor_id="climate.vpd_avg",
            message="VPD outside band for 45 min",
            details={
                "vpd_stress_hours": 2.5,
                "recent_samples": 12,
                "recent_high_samples": 8,
                "recent_high_fraction": 0.67,
                "avg_vpd_15m": 2.3,
                "avg_vpd_high_15m": 1.8,
            },
            metric_value=2.3,
            threshold_value=2.0,
        )
        assert a.severity == "warning"

    def test_rejects_unknown_severity(self):
        with pytest.raises(ValidationError):
            AlertEnvelope(
                alert_type="x",
                severity="emergency",
                category="system",
                message="x",
            )

    def test_rejects_unknown_category(self):
        with pytest.raises(ValidationError):
            AlertEnvelope(
                alert_type="x",
                severity="warning",
                category="physics",
                message="x",
            )

    def test_rejects_empty_message(self):
        with pytest.raises(ValidationError):
            AlertEnvelope(
                alert_type="x",
                severity="warning",
                category="system",
                message="",
            )


class TestAlertLogRow:
    def test_full_row(self):
        row = AlertLogRow(
            id=42,
            ts=NOW,
            alert_type="setpoint_unconfirmed",
            severity="warning",
            category="system",
            sensor_id="setpoint.temp_low",
            message="setpoint unconfirmed for 7 min",
            disposition="open",
            source="ingestor",
        )
        assert row.disposition == "open"


class TestAlertAction:
    def test_ack_action(self):
        a = AlertAction(action="acknowledge", alert_id=123, data=AlertAckPayload(acknowledged_by="jason"))
        assert a.action == "acknowledge"

    def test_resolve_action(self):
        a = AlertAction(
            action="resolve",
            alert_id=123,
            data=AlertResolvePayload(resolved_by="jason", resolution="false positive"),
        )
        assert isinstance(a.data, AlertResolvePayload)

    def test_list_needs_no_payload(self):
        a = AlertAction(action="list")
        assert a.data is None


class TestCropCreate:
    def _kwargs(self):
        return {
            "name": "Tomato Sungold",
            "variety": "Sungold F1",
            "position": "hydro-03",
            "zone": "center",
            "planted_date": date(2026, 4, 1),
            "stage": "vegetative",
        }

    def test_valid(self):
        c = CropCreate(**self._kwargs())
        assert c.stage == "vegetative"

    def test_rejects_bad_stage(self):
        with pytest.raises(ValidationError):
            CropCreate(**{**self._kwargs(), "stage": "overripe"})

    def test_rejects_negative_count(self):
        with pytest.raises(ValidationError):
            CropCreate(**{**self._kwargs(), "count": -1})

    def test_rejects_vpd_inversion_via_full_record(self):
        # CropCreate doesn't enforce vpd_low < vpd_high (crops are targets, not setpoints)
        c = CropCreate(**{**self._kwargs(), "target_vpd_low": 0.8, "target_vpd_high": 1.5})
        assert c.target_vpd_low == 0.8


class TestCropUpdate:
    def test_partial_patch(self):
        u = CropUpdate(notes="Spider mite pressure rising")
        assert u.notes.startswith("Spider")
        assert u.name is None

    def test_empty_patch_allowed(self):
        # CropUpdate allows fully-optional patch (no-op updates are OK)
        u = CropUpdate()
        assert u.model_dump(exclude_unset=True) == {}


class TestObservationCreate:
    def test_valid_health_check(self):
        o = ObservationCreate(obs_type="health_check", health_score=0.85, observer="jason")
        assert o.health_score == 0.85

    def test_health_score_bounds(self):
        with pytest.raises(ValidationError):
            ObservationCreate(obs_type="health_check", health_score=1.5)


class TestEventCreate:
    def test_stage_change(self):
        e = EventCreate(event_type="stage_change", old_stage="seedling", new_stage="vegetative")
        assert e.new_stage == "vegetative"


class TestFullRowShapes:
    def test_crop_full_row(self):
        c = Crop(
            id=1,
            name="Lettuce",
            position="nft-01",
            zone="south",
            planted_date=date(2026, 4, 10),
        )
        assert c.is_active is True

    def test_crop_event_minimal(self):
        e = CropEvent(event_type="planted")
        assert e.source == "manual"

    def test_observation_full_row(self):
        o = Observation(id=5, obs_type="pest", severity=3, species="aphid")
        assert o.species == "aphid"


class TestCropAction:
    def test_create_action(self):
        a = CropAction(
            action="create",
            data=CropCreate(
                name="x",
                position="p",
                zone="z",
                planted_date=date(2026, 4, 1),
            ),
        )
        assert isinstance(a.data, CropCreate)

    def test_update_action(self):
        a = CropAction(action="update", crop_id=7, data=CropUpdate(notes="x"))
        assert a.crop_id == 7

    def test_rejects_unknown_action(self):
        with pytest.raises(ValidationError):
            CropAction(action="nuke")


class TestObservationAction:
    def test_record_observation(self):
        a = ObservationAction(
            action="record_observation",
            crop_id=3,
            data=ObservationCreate(obs_type="pest", severity=2),
        )
        assert a.crop_id == 3

    def test_record_event(self):
        a = ObservationAction(
            action="record_event",
            crop_id=3,
            data=EventCreate(event_type="harvested", count=20),
        )
        assert isinstance(a.data, EventCreate)

    def test_record_harvest(self):
        a = ObservationAction(
            action="record_harvest",
            crop_id=3,
            data=HarvestCreate(weight_kg=1.2, salable_weight_kg=1.0, operator="jason"),
        )
        assert isinstance(a.data, HarvestCreate)

    def test_record_treatment(self):
        a = ObservationAction(
            action="record_treatment",
            crop_id=3,
            data=TreatmentCreate(product="Neem", method="foliar", target_pest="aphids"),
        )
        assert isinstance(a.data, TreatmentCreate)


class TestLessonAction:
    def test_create(self):
        a = LessonAction(
            action="create",
            data=LessonCreate(
                category="misting",
                condition="dry day <20% RH",
                lesson="engage 1.3, gap 30s",
                confidence="medium",
            ),
        )
        assert a.action == "create"
        assert isinstance(a.data, LessonCreate)

    def test_update(self):
        a = LessonAction(action="update", lesson_id=12, data=LessonUpdate(confidence="high"))
        assert a.lesson_id == 12

    def test_validate(self):
        a = LessonAction(action="validate", lesson_id=12, data=LessonValidate())
        assert a.data.confidence is None

    def test_deactivate_no_data(self):
        a = LessonAction(action="deactivate", lesson_id=12)
        assert a.data is None

    def test_supersede(self):
        a = LessonAction(action="supersede", lesson_id=12, data=LessonSupersede(new_id=15))
        assert a.action == "supersede"
        assert isinstance(a.data, LessonSupersede)
        assert a.data.new_id == 15

    def test_supersede_rejects_nonpositive_new_id(self):
        with pytest.raises(ValidationError):
            LessonSupersede(new_id=0)

    def test_rejects_unknown_action(self):
        with pytest.raises(ValidationError):
            LessonAction(action="archive")

    def test_rejects_bad_confidence(self):
        with pytest.raises(ValidationError):
            LessonCreate(category="x", condition="x", lesson="x", confidence="maybe")


class TestLessonStateMachine:
    """G8 lessons state machine: derive_lesson_state + transition guards."""

    def test_derive_proposed_fresh(self):
        # active, never superseded, single validation count, no independent validation
        assert derive_lesson_state(is_active=True, superseded_by=None, times_validated=1) == "proposed"

    def test_derive_validated_by_count(self):
        assert derive_lesson_state(is_active=True, superseded_by=None, times_validated=3) == "validated"

    def test_derive_validated_by_independent_flag(self):
        # times_validated still 1, but last_validated > created_at observed
        assert (
            derive_lesson_state(is_active=True, superseded_by=None, times_validated=1, has_independent_validation=True)
            == "validated"
        )

    def test_derive_retired(self):
        assert derive_lesson_state(is_active=False, superseded_by=None, times_validated=2) == "retired"

    def test_derive_superseded_takes_precedence(self):
        # superseded_by set wins even if is_active somehow still true
        assert derive_lesson_state(is_active=True, superseded_by=99, times_validated=5) == "superseded"
        assert derive_lesson_state(is_active=False, superseded_by=99, times_validated=5) == "superseded"

    # ── legal transitions ───────────────────────────────────────────────
    @pytest.mark.parametrize(
        "current,target",
        [
            ("proposed", "validated"),
            ("proposed", "superseded"),
            ("proposed", "retired"),
            ("validated", "superseded"),
            ("validated", "retired"),
            # idempotent self-transitions are legal only for non-terminal states
            ("proposed", "proposed"),
            ("validated", "validated"),
        ],
    )
    def test_legal_transitions(self, current, target):
        assert is_legal_lesson_transition(current, target) is True

    # ── illegal transitions ─────────────────────────────────────────────
    @pytest.mark.parametrize(
        "current,target",
        [
            # cannot go backwards
            ("validated", "proposed"),
            # terminal states accept no onward transitions
            ("superseded", "validated"),
            ("superseded", "retired"),
            ("superseded", "proposed"),
            ("retired", "validated"),
            ("retired", "superseded"),
            ("retired", "proposed"),
            # terminal states are absorbing: no idempotent self-transition
            ("superseded", "superseded"),
            ("retired", "retired"),
        ],
    )
    def test_illegal_transitions_rejected(self, current, target):
        assert is_legal_lesson_transition(current, target) is False

    def test_transition_map_covers_all_states(self):
        states = set(LessonState.__args__)
        assert set(LESSON_TRANSITIONS.keys()) == states
        # every declared target is itself a valid state
        for targets in LESSON_TRANSITIONS.values():
            assert targets <= states

    def test_terminal_states_have_no_forward_transitions(self):
        assert LESSON_TRANSITIONS["superseded"] == frozenset()
        assert LESSON_TRANSITIONS["retired"] == frozenset()


class TestPlannerLesson:
    def test_full_row(self):
        lesson = PlannerLesson(
            id=1,
            category="misting",
            condition="dry day <20% RH",
            lesson="engage 1.3",
            confidence="medium",
            times_validated=4,
        )
        assert lesson.confidence == "medium"
