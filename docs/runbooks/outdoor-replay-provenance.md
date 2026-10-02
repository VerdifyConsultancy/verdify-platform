# Outdoor replay age provenance (#419)

`scripts/export-replay-overrides.sh [days]` makes read-only source queries and
writes a local replay CSV. Set `VERDIFY_DB_BACKEND=kube` for the product database
and `OUTDIR` to an exclusive evidence directory. This does not update the
controller, alerts, or the database. The default output is checkout-local.

The exact-age stream is migration 267's original `system_state` source events
for `climate_moisture_exchange`, not the periodically flushed
`climate_action_log` cache. Each replay row uses only events with `source_ts`
at or before its climate timestamp. Age is the reported device age plus elapsed
callback receive time, rounded upward. Receive time is explicitly not a raw
Tempest sample timestamp. Original event ID, payload hash, runtime UUID,
generation, reported age, and callback timestamp accompany the result.

The first weather callback per runtime/generation is excluded because
SubscribeStates can return a cached value. Migration 262's original connected
and gap events invalidate retained weather authority. At equal timestamps,
transport boundaries win. Later missing/malformed ages remain unavailable;
99999 and an explicit `outdoor_fresh=false` remain stale. Sentinel callbacks
retain their original lineage even when no climate row falls in their interval.
The as-of merge never consumes a future callback; the pure age projector also
refuses future event time.

Before exact-age capability first appears, historical rows retain the existing
`conservative_change_observation` proxy. That label is never changed to device
provenance. After capability appears, unavailable events cannot fall back to the
historical proxy. The replay harness reports historical proxy, original device,
and original stale row counts separately; existing coverage thresholds remain
unchanged.

## Qualification and limits

`tests/test_replay_outdoor_device_age.py` exercises temporal causality, reconnect
cache exclusion, missing/malformed/sentinel events, and the exporter's actual
embedded merge. The stock corpus remains a historical replay qualification;
its passing branch totals do not prove every measured-device branch has been
observed in production. A bounded native post-OTA extraction must retain its
raw query/output, first-callback exclusions, and unavailable/sentinel rows.
Do not reconstruct old device age from cache flush timestamps or relabel a
short native sample as full-regime coverage.

No firmware control logic, device entity, schema, or service startup changes
are needed for this exporter correction. Restart: none.
