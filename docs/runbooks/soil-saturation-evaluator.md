# Sustained saturation foundation (#297 S6 packet)

`ingestor/soil_saturation.py` provides a pure, alert-only decision evaluator with
explicit measured-target, probe/calibration and evidence identities. It requires
continuous timestamp coverage, fresh valid positive readings, a sustained high
threshold and separately sustained hysteretic recovery. Missing, stale, corrupt,
gapped or wrong-source truth returns unavailable; it never clears an active latch.
The bounded synthetic fixtures are not commissioned targets.

The actual ingestor scheduler runs an hourly status probe. It explicitly reports
`available=False reason=uncommissioned_targets actuation=disabled`, makes no DB
query, writes no alert row and calls no device or irrigation function. The pure
evaluator and this disabled status probe are the S6 deliverable, not an operating
saturation alarm. Later activation requires measured thresholds, a real read-side
source adapter and a reviewed durable alert-lifecycle integration. Do not substitute
seeded wilt thresholds or invent soil water capacity from historical readings.

Issue #297 remains open. Its irrigation skip/shrink and physical acceptance are
outside this packet and blocked on #45. No firmware or irrigation behavior changes.
Verify the delivered image digest, the live hourly status message, the singleton
ESP32 connection, and five minutes without schema-validation errors after rolling
the single ingestor with its existing Recreate strategy.
