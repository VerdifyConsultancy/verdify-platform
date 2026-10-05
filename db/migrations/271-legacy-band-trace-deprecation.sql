-- #424 forward-only explicit deprecation of historical SQL labels.
-- Preserve OIDs/result signatures used by read-only compatibility consumers.
-- Public API already uses fn_public_band_trace_v2; these legacy aliases cannot
-- grant device-consumption proof. No telemetry/delivery rows are rewritten.
COMMENT ON FUNCTION public.fn_band_trace(timestamptz,timestamptz,text) IS
'DEPRECATED legacy diagnostic: crop_* is current server resolver reconstruction; fw_* is desired/dispatched setpoint_changes history, including unconfirmed or failed requests, never firmware consumption; rb_* is database snapshot without original callback age or connection generation. readback_matches_fw_* is a numeric diagnostic, never controller-confirmation proof. Use fn_public_band_trace_v2 for public lineage and original source-bound passive callback epochs for consumed-state qualification.';
COMMENT ON FUNCTION public.fn_band_timeline(timestamptz,timestamptz,interval,text) IS
'DEPRECATED physical provenance aliases: projected_* and crop_* reconstruct the current server resolver; actual_* and firmware_* are historical requested values or database snapshots, not qualified device consumption. Threshold fields reconstruct server policy. Preserve for plotting diagnostics only; consumed-band readiness requires fresh original callback epochs and independent source-bound wire projection.';
COMMENT ON FUNCTION public.fn_band_setpoint_provenance(timestamptz,text) IS
'DEPRECATED firmware_setpoint_value label: setpoint_changes is desired/dispatched history and does not prove confirmed delivery or firmware consumption. cfg_readback_value is a database snapshot lacking original callback/runtime generation custody. fn_public_band_trace_v2 exposes truthful public lineage; source-bound six-series passive evidence is required for readiness.';
COMMENT ON FUNCTION public.fn_setpoint_at(text,text,timestamptz) IS
'Latest nonexpired desired/dispatched setpoint_changes history at a timestamp, regardless of confirmation status. Compatibility diagnostic only; neither physical readback nor consumed-state evidence.';
COMMENT ON VIEW public.v_band_trace_latest IS
'DEPRECATED legacy aliases from fn_band_trace: desired/dispatched history and database snapshots, not controller-confirmed or consumed state. Use public lineage v2 and original generation-bound callbacks.';
COMMENT ON VIEW public.v_band_trace_recent IS
'DEPRECATED legacy aliases from fn_band_trace: current reconstruction and requested history, not immutable crop history or controller-consumed-state evidence.';
