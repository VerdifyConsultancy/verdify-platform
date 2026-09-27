-- Gate P may start the exact study's baseline-before recovery while its
-- already sealed, older runtime fault is still truthfully open. This read-only
-- projection grants no lifecycle, device or proof authority. The latest fault
-- remains open until new full-baseline work records a later real recovery.

CREATE OR REPLACE FUNCTION public.fn_experiment_v2_gate_p_sealed_recovery_target(
    p_experiment_id uuid
) RETURNS boolean
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $body$
    SELECT p_experiment_id = '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid
       AND EXISTS (
           SELECT 1 FROM public.control_experiments e
           WHERE e.experiment_id = p_experiment_id
             AND e.protocol_version = 2
             AND e.status = 'draft'
             AND e.execution_phase = 'shadow'
             AND e.admission_state = 'closed'
             AND e.component_enabled = false
             AND e.lease_generation = 17
             AND e.revision_bundle_sha256 =
                 '3e26a2da1863bd14d255d58fe00d8e94aae9226f9b588ddd9d4f993e6bcb7016'
             AND NOT EXISTS (
                 SELECT 1 FROM public.experiment_v2_exposures exposure
                 LEFT JOIN public.experiment_v2_exposure_closures closure
                   USING (exposure_id)
                 WHERE exposure.experiment_id = e.experiment_id
                   AND closure.exposure_id IS NULL)
             AND NOT EXISTS (
                 SELECT 1 FROM public.experiment_v2_direct_proof_receipts proof
                 WHERE proof.experiment_id = e.experiment_id))
       AND EXISTS (
           SELECT 1
           FROM public.experiment_v2_direct_proof_emergency_recovery_receipts receipt
           JOIN public.experiment_v2_direct_proof_emergency_resolutions resolution
             USING (resolution_id)
           JOIN public.experiment_v2_direct_proof_authorizations authz
             ON authz.authorization_id = receipt.authorization_id
           WHERE receipt.experiment_id = p_experiment_id
             AND receipt.resolution_id =
                 'ace2d26c-539d-4007-ad9c-d25ad812644f'::uuid
             AND receipt.authorization_id =
                 'd00304d1-74f9-4872-857e-6944de53ac46'::uuid
             AND receipt.recovery_work_id =
                 '7093f8c3-a36e-49f2-8b4b-443d32a9a51b'::uuid
             AND receipt.recovery_evidence_sha256 =
                 '0fa6d172de87cf2008d5908ff4a3517eeca1d1cd4811e86461fc349c25f41b91'
             AND resolution.resolution_kind = 'bounded_baseline_recovery')
       AND EXISTS (
           SELECT 1
           FROM public.experiment_v2_direct_proof_authorizations authz
           WHERE authz.authorization_id =
                 'd00304d1-74f9-4872-857e-6944de53ac46'::uuid
             AND authz.experiment_id = p_experiment_id
             AND authz.attempt_number = (
                 SELECT max(latest.attempt_number)
                 FROM public.experiment_v2_direct_proof_authorizations latest
                 WHERE latest.experiment_id = p_experiment_id)
             AND EXISTS (
                 SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events failed
                 WHERE failed.authorization_id = authz.authorization_id
                   AND failed.event_kind = 'failed')
             AND NOT EXISTS (
                 SELECT 1 FROM public.experiment_v2_direct_proof_attempt_events superseded
                 WHERE superseded.authorization_id = authz.authorization_id
                   AND superseded.event_kind = 'superseded'))
       AND EXISTS (
           SELECT 1 FROM public.experiment_v2_runtime_faults fault
           WHERE fault.experiment_id = p_experiment_id
             AND fault.fault_report_id =
                 '725306a0-9b4b-4083-a3ba-8912308e1d55'::uuid
             AND fault.reported_fault_kind = 'db_outage'
             AND fault.close_reason = 'writer_collision'
             AND fault.recovery_work_id IS NULL
             AND fault.admission_state_after = 'emergency_hold'
             AND fault.facility_authority_yielded
             AND NOT EXISTS (
                 SELECT 1 FROM public.experiment_v2_runtime_faults later
                 WHERE later.experiment_id = fault.experiment_id
                   AND later.recorded_at >= fault.recorded_at
                   AND later.fault_report_id <> fault.fault_report_id)
             AND NOT EXISTS (
                 SELECT 1 FROM public.experiment_v2_work_events recovered
                 WHERE recovered.experiment_id = fault.experiment_id
                   AND recovered.event_kind = 'recovered'
                   AND recovered.recorded_at >= fault.recorded_at))
       AND EXISTS (
           SELECT 1 FROM public.fn_experiment_v2_ops_status() ops
           WHERE ops.experiment_id = p_experiment_id
             AND ops.safety_state = 'runtime_fault'
             AND ops.alert_reason = 'runtime_fault_requires_recovery'
             AND ops.open_exposure_count = 0
             AND ops.observation_truth = 'no_current_work');
$body$;

ALTER FUNCTION public.fn_experiment_v2_gate_p_sealed_recovery_target(uuid)
    OWNER TO verdify_experiment_v2_owner;
REVOKE ALL PRIVILEGES ON FUNCTION
    public.fn_experiment_v2_gate_p_sealed_recovery_target(uuid) FROM PUBLIC CASCADE;
GRANT EXECUTE ON FUNCTION
    public.fn_experiment_v2_gate_p_sealed_recovery_target(uuid)
    TO verdify_ingestor_runtime;
