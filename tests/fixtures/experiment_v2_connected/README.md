# Connected randomized qualification (synthetic)

This fixture qualifies source behavior only. Its clock, cfg epochs, selector
response, transport and endpoints are synthetic. It does not establish physical
readiness, authorise a real experiment, or supply pilot observations.

Run only against a disposable restored database named `verdify_rehearsal`,
PostgreSQL port 55432 on `/run/postgresql`, with `listen_addresses=''` and network
deny-all. The ordinary executor and randomizer login roles call the genuine
security-definer APIs. The owner sets fixture prerequisites and endpoint inputs.
The clock installer records original function hashes and changes only clock
calls in experiment-owner functions; it is not a migration.

Order:

1. Owner `psql -X -v ON_ERROR_STOP=1 -f install-test-clock.sql`.
2. Owner `psql -X -v ON_ERROR_STOP=1 -f setup-randomized.sql`.
3. With ingestor runtime dependencies and source imports available, execute
   `python scripts/qualify-v2-connected-clock.py` in the disposable runtime.
4. Owner `psql -X -v ON_ERROR_STOP=1 -f freeze-export.sql`.
5. Read exact `experiment_v2_exports.export_payload::text` bytes, removing only
   psql's final line delimiter. Retain its stored `export_sha256`. Read the private
   `experiment_v2_randomization.x_physical_arm` mapping only after freeze.
6. Run `scripts/analyze-v2-connected-clock-export.py EXPORT --sha256 HASH
   --x-physical-arm A_OR_B`. It analyzes twice, requires identical results, all
   six assigned days, no pair replacement, and the truthful null conclusion.

The fixture locks three pairs once, verifies finalization retry preserves the
same draw, and records each genuine selector choice idempotently. Each day uses
canonical 48-command FakeTransport baseline recovery, followed by its genuine
randomized target. The second nonbaseline target injects a command failure; the
third injects a reset. All six assignment rows are retained and finalized. The
freezer derives failure/fallback flags from the actual journal and attaches
explicitly synthetic endpoints: five zero days and one null day. Freeze retry
preserves the same immutable export.

`physical_execution_qualified` is overridden only inside this standalone
process so FakeTransport can traverse genuine executor code. It must never be
used in a production process. No production configuration changes are required.
