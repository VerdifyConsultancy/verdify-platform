\set ON_ERROR_STOP on
-- Read-only durability witness. Caller supplies independently retained day,
-- receipt ID and hash; this never creates or repairs a route publication.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '15s';
SELECT current_database() = 'verdify_rehearsal'
    AND inet_server_addr() IS NULL
    AND current_setting('listen_addresses') = '' AS isolated_restore_verified
\gset
\if :isolated_restore_verified
\else
    \echo 'FATAL: socket-only disposable restored database required'
    DO $$ BEGIN RAISE EXCEPTION 'restored native route qualification failed'; END $$;
\endif
SELECT count(*) = 1 AND coalesce(bool_and(
    r.day = :'route_day'::date
    AND r.projection_sha256 = :'route_projection_sha256'
    AND r.projection_sha256 = encode(sha256(convert_to(r.projection::text, 'UTF8')), 'hex')
    AND (r.projection->>'route_18h_available')::boolean
    AND (r.projection->>'source_continuity_verified')::boolean
    AND jsonb_array_length(r.projection->'bins') = 72
    AND NOT (r.projection->>'physical_proof_eligible')::boolean
    AND NOT (r.projection->>'experiment_endpoint_eligible')::boolean
    AND NOT (r.projection->>'causal_effect_estimate')::boolean
), false) AS restored_native_receipt_verified
FROM public.fixed_panel_native_day_receipts r
WHERE r.receipt_id = :'route_receipt_id'::bigint
\gset
\if :restored_native_receipt_verified
\else
    \echo 'FATAL: restored native route receipt differs from retained source receipt'
    DO $$ BEGIN RAISE EXCEPTION 'restored native route qualification failed'; END $$;
\endif
SELECT receipt_id = :'route_receipt_id'::bigint
    AND projection_sha256 = :'route_projection_sha256'
    AND route_18h_available AND source_continuity_verified
    AND NOT physical_serial_verified AND NOT modbus_poll_time_verified
    AND NOT physical_proof_eligible AND NOT experiment_endpoint_eligible
    AND NOT causal_effect_estimate AS restored_native_reader_verified
FROM public.fn_fixed_panel_native_route_day_receipt(:'route_day'::date, 'vallery')
\gset
\if :restored_native_reader_verified
\else
    \echo 'FATAL: restored bounded native route reader differs from retained source receipt'
    DO $$ BEGIN RAISE EXCEPTION 'restored native route qualification failed'; END $$;
\endif
SELECT jsonb_build_object(
    'qualification', 'restored-native-route-receipt-v1',
    'day', :'route_day', 'receipt_id', :'route_receipt_id'::bigint,
    'projection_sha256', :'route_projection_sha256',
    'immutable_receipt_verified', :'restored_native_receipt_verified'::boolean,
    'bounded_reader_verified', :'restored_native_reader_verified'::boolean,
    'physical_proof_eligible', false, 'experiment_endpoint_eligible', false
);
COMMIT;
