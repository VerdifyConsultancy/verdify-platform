BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout='2s';
SET LOCAL lock_timeout='500ms';
SET LOCAL idle_in_transaction_session_timeout='15s';
SET LOCAL work_mem='4MB';
SET LOCAL max_parallel_workers_per_gather=0;
SET LOCAL timezone='UTC';
SELECT jsonb_build_object('surface','identity','captured_at',clock_timestamp(),'database',current_database(),'role',current_user,'server_addr',inet_server_addr(),'postmaster_start',pg_postmaster_start_time(),'read_only',current_setting('transaction_read_only'));
SELECT jsonb_build_object('surface','diagnostics','row',to_jsonb(q)) FROM (
 SELECT ts,greenhouse_id,firmware_version,heap_bytes,heap_min_free_kb,heap_largest_free_block_kb,uptime_s,reset_reason,probe_health,active_probe_count,wifi_rssi,controller_time_epoch,sntp_valid
 FROM public.diagnostics WHERE greenhouse_id='vallery' AND ts>=statement_timestamp()-interval '24 hours' ORDER BY ts DESC LIMIT 80
) q;
SELECT jsonb_build_object('surface','heap_profile','row',to_jsonb(q)) FROM (
 SELECT ts,level,tag,message FROM public.esp32_logs WHERE ts>=statement_timestamp()-interval '24 hours' AND tag='heap_profile' ORDER BY ts DESC LIMIT 16
) q;
SELECT jsonb_build_object('surface','reset_reason_log','row',to_jsonb(q)) FROM (
 SELECT ts,level,tag,left(message,256) AS message FROM public.esp32_logs WHERE ts>=statement_timestamp()-interval '24 hours' AND tag='debug' AND message LIKE 'Reset Reason:%' ORDER BY ts DESC LIMIT 4
) q;
ROLLBACK;
