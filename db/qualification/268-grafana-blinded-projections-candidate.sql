-- #643 source-owned blinded projection candidate for reserved268.
-- NOT an applied/ledgered migration; no grants or credentials are installed here.
-- Namespace and runtime identity creation belongs to the complete six-role migration.
-- Preserve source panels without granting mapping/hash/control table access.

CREATE VIEW verdify_grafana_runtime.blinded_exposure_coverage
WITH (security_barrier=true, security_invoker=false) AS
SELECT started_at, ended_at, identity_confirmed
FROM public.policy_exposures;

CREATE VIEW verdify_grafana_runtime.blinded_activation_identity
WITH (security_barrier=true, security_invoker=false) AS
SELECT updated_at, observed_activation_sha256 IS NOT NULL AS has_observed_identity,
       observed_activation_sha256 = expected_activation_sha256 AS identity_ok
FROM public.policy_exposures;

CREATE VIEW verdify_grafana_runtime.blinded_activation_lineage
WITH (security_barrier=true, security_invoker=false) AS
SELECT a.assignment_id, lower(a.valid_range) AS starts_at,
       o.staged_at, o.activated_at, o.activated_at-o.created_at AS delivery_lag,
       e.identity_confirmed, e.coverage_fraction, e.close_reason
FROM public.control_assignments a
LEFT JOIN LATERAL (
    SELECT ob.staged_at, ob.activated_at, ob.created_at
    FROM public.policy_delivery_outbox ob
    JOIN public.effective_policy_vectors v ON v.vector_id=ob.vector_id
    WHERE v.assignment_id=a.assignment_id
    ORDER BY ob.created_at DESC LIMIT 1
) o ON true
LEFT JOIN LATERAL (
    SELECT ex.identity_confirmed, ex.coverage_fraction, ex.close_reason
    FROM public.policy_exposures ex
    WHERE ex.assignment_id=a.assignment_id
    ORDER BY ex.started_at DESC LIMIT 1
) e ON true;

CREATE VIEW verdify_grafana_runtime.blinded_experiment_lifecycle
WITH (security_barrier=true, security_invoker=false) AS
SELECT kind, status, timezone, started_at, updated_at
FROM public.control_experiments;

CREATE VIEW verdify_grafana_runtime.blinded_experiment_events
WITH (security_barrier=true, security_invoker=false) AS
SELECT recorded_at, event_kind, severity, actor FROM public.experiment_events;

ALTER VIEW verdify_grafana_runtime.blinded_experiment_events OWNER TO verdify;
REVOKE ALL ON verdify_grafana_runtime.blinded_experiment_events FROM PUBLIC;
ALTER VIEW verdify_grafana_runtime.blinded_exposure_coverage OWNER TO verdify;
ALTER VIEW verdify_grafana_runtime.blinded_activation_identity OWNER TO verdify;
ALTER VIEW verdify_grafana_runtime.blinded_activation_lineage OWNER TO verdify;
ALTER VIEW verdify_grafana_runtime.blinded_experiment_lifecycle OWNER TO verdify;
REVOKE ALL ON verdify_grafana_runtime.blinded_exposure_coverage,
    verdify_grafana_runtime.blinded_activation_identity,
    verdify_grafana_runtime.blinded_activation_lineage,
    verdify_grafana_runtime.blinded_experiment_lifecycle FROM PUBLIC;
-- Only verdify_grafana_runtime receives SELECT in the complete role migration.
