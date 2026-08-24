"""
tasks — Periodic background tasks absorbed from standalone cron scripts.

Each function is an async coroutine that takes an asyncpg.Pool and runs one
unit of work. Called by task_loop() in ingestor.py on defined intervals.

Replaces 10 cron jobs with a single in-process task scheduler.

This package was split from a single ~8050-line tasks.py module (issue #46).
It re-exports the unchanged public import surface so every
`from tasks import X` / `import tasks; tasks.X` continues to resolve.
Behaviour is identical: function bodies are byte-for-byte the originals.
"""

# Bind every submodule as a package attribute so `tasks.<submodule>` resolves
# even after a `sys.modules.pop("tasks"); import tasks` round-trip (the test
# suite does this, and the submodules stay cached). `from .X import *` alone does
# not rebind the submodule attribute when the submodule is already in sys.modules.
from . import (  # noqa: F401
    _common,
    alerts,
    band_anchors,
    component_experiment,
    confirmation,
    daily,
    dispatcher,
    experiment_assignments,
    experiment_qualification,
    forecast,
    ha,
    heartbeat,
    policy_arbiter,
    policy_delivery,
    watch,
)
from ._common import *  # noqa: F401,F403
from ._common import (  # noqa: F401
    _CENTER_FEEDBACK_MAP,
    _DENVER,
    _FORECAST_DEVIATION_DEFAULTS,
    _FORECAST_DEVIATION_SIGMA_HISTORY_DAYS,
    _FORECAST_DEVIATION_SIGMA_MULTIPLIER,
    _FORECAST_STALE_SENSOR_ID,
    _FORECAST_STALE_THRESHOLD_S,
    _FORECAST_URL,
    _GL_SQL,
    _GRADE_RELAYS,
    _GRADED_ZONES,
    _HA_STATE_FILE,
    _HA_SWITCHES,
    _HYDRO_MAP,
    _LIGHT_ENTITIES,
    _LOCATION,
    _MILESTONE_STATE_FILE,
    _OCCUPANCY_ENTITIES,
    _PHYSICS_INVARIANTS,
    _POPULATE_SITE_CONTENT_SCRIPT,
    _READBACKABLE_PARAMS,
    _SHELLY_ENTITIES,
    _SHELLY_PREFIX,
    _TEMPEST_MAP,
    _expected_firmware_version,
    _extract_node_snapshot,
    _fetch_all_cpu_samples,
    _fetch_all_gpu_power_samples,
    _fetch_cpu_sample,
    _fetch_gpu_power_samples,
    _fetch_ha_batch,
    _fetch_ha_entity,
    _fetch_url_text,
    _ha_prev_state,
    _ha_state,
    _iter_prometheus_samples,
    _last_forecast_fetch,
    _last_pushed,
    _leak_counter,
    _leak_state,
    _load_token,
    _midnight_watch_last_date,
    _milestones_cache,
    _milestones_date,
    _milestones_fired,
    _parse_prometheus_labels,
    _post_slack,
    _slack_brief_last_fire,
    _sp,
    _sun,
    _td,
)
from .alerts import *  # noqa: F401,F403
from .component_experiment import (  # noqa: F401
    attest_component_safe_startup,
    component_experiment_worker,
    create_component_experiment_pool,
    prime_component_startup_hold,
    record_component_cfg_readback,
    record_component_device_uptime,
)
from .confirmation import *  # noqa: F401,F403
from .daily import *  # noqa: F401,F403
from .daily import (  # noqa: F401
    _auto_close_disposition,
    _build_relay_forward_fill,
    _build_zone_band_timeline,
    _classify_cold_feasibility,
    _classify_hot_feasibility,
    _classify_vpd_high_feasibility,
    _classify_vpd_low_feasibility,
    _electric_wattages,
    _grade_credit,
    _graded_house_axis,
    _GradedComplianceAccumulator,
    _refresh_daily_summary_for_date,
    _runtime_kwh_from_minutes,
    _vpd_stress_alert_threshold,
    _write_graded_compliance,
    _zone_band_at,
    _zone_score,
)
from .dispatcher import *  # noqa: F401,F403
from .dispatcher import (  # noqa: F401
    _activity_defaults_from_lighting,
    _ai_moisture_stress_policy_supported,
    _align_activity_defaults_with_planned_lighting,
    _apply_manual_overlay,
    _coerce_registry_value,
    _control_band_from_house_row,
    _direct_wet_policy_supported,
    _dispatch_source,
    _fetch_moisture_guard_context,
    _fetch_quiet_state,
    _finite_positive,
    _heap_push_defer_active,
    _house_vpd_control_band,
    _median,
    _readback_drift,
    _record_quiet_mode,
    _should_skip,
    _upsert_change,
    _validate_physics,
    _vpd_high_moisture_guardrails,
    _write_clamp_audit_rows,
)

# Lane C (#584): coroutine names differ from their module names on purpose so
# the submodule attributes bound above survive (see the rebinding note in the
# module docstring); import them explicitly instead of star-importing.
from .experiment_assignments import (  # noqa: F401
    EXPERIMENT_OWNED_PARAMS,
    experiment_assignment_scheduler,
)
from .experiment_qualification import experiment_qualification_scheduler  # noqa: F401
from .forecast import *  # noqa: F401,F403
from .forecast import (  # noqa: F401
    _cloud_cover_proxy_pct,
    _fetch_forecast,
    _first_float,
    _forecast_deviation_alert_payload,
    _forecast_deviation_threshold_map,
    _forecast_deviation_trigger_from_alert,
    _insert_forecast_deviation_alert,
    _jsonb_dict,
    _outdoor_vpd_kpa,
    _pending_forecast_deviation_alert,
    _resolve_forecast_deviation_alert,
)
from .ha import *  # noqa: F401,F403
from .ha import (  # noqa: F401
    _load_site_content_populator,
    _refresh_site_content_corpus,
)
from .heartbeat import *  # noqa: F401,F403
from .heartbeat import (  # noqa: F401
    _compute_milestones,
    _deliver_and_log,
    _deviation_expected_at,
    _ensure_deviation_trigger_ledger,
    _ensure_expected_planner_triggers,
    _expected_action_for_event,
    _expire_planner_trigger_slas,
    _load_milestone_state,
    _log_plan_delivery,
    _mark_expected_trigger_delivered,
    _milestone_due_at,
    _milestone_event,
    _resolve_delivery_log,
    _save_milestone_state,
    _sla_seconds,
    _sync_planner_trigger_ledger,
    _trigger_spec_for_event,
)
from .policy_arbiter import (  # noqa: F401
    policy_arbiter_admissions,
    treatment_bytes_for_assignment,
)
from .policy_delivery import policy_delivery_worker  # noqa: F401
from .watch import *  # noqa: F401,F403
