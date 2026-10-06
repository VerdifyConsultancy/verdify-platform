# Qualified physical crop-band publication

Physical crop evidence is separate from controller attribution credit, legacy
house-average scored readings, and native route observations. Migration 274
installs an empty append-only publication store and bounded API/MCP reader;
it inserts no production evidence. An absent revision remains
`publication_not_qualified` with a null diagnostic and no physical percentage.

Only the existing database owner `verdify`, authenticated as both current and
session user, can publish a completed Denver-day revision. The owner must first
independently authenticate historical immutable crop targets, the fixed
north/east/west hardware placement and identity, fresh per-probe observation
times, and the calculation inputs. Schema consistency, artifact hashes, a route
mapping, and an operator's unsupported assertion do not establish those facts.
Do not use the native route collector or the database flush timestamps to fill
physical proof. The current estate's physical commissioning remains unavailable.

After genuine evidence exists, use `scripts/publish-physical-crop-band-evidence.py`
with an existing owner `DB_DSN` supplied securely in the process environment:
provide `--diagnostic`, four reviewed local input files through
`--target-manifest`, `--panel-manifest`, `--input`, and
`--calculation-source`, and a bounded written
`--authenticity-statement`. Those four arguments are file paths: the producer hashes their exact bytes and requires each to match
its diagnostic hash. It validates the existing strict physical model before
calling the owner-only SQL publication function. The SQL function revalidates
scope, full-day completion, qualification bindings, counts, missingness,
percentages and distances. Store the authentic files and their custody receipt
durably before publishing; the database binds their hashes rather than storing
or authenticating hardware artifacts itself. Publication returns only revision,
date, greenhouse and artifact hash metadata.

A correction appends a new revision. Updates and deletes are rejected. Readers
serve the latest immutable revision for the requested date; a malformed latest
row is withheld rather than falling back to an older favorable result. API and
MCP runtime duties have bounded reader execution only, with no raw table or
sequence access and no publication authority. The API scorecard, public evidence
projection, and Iris's registered MCP `scorecard` tool receive the same typed
physical evidence. Iris already reads that JSON; the standalone planner's scalar
DB adapter has no physical reader grant and is not claimed as a physical runtime
consumer.

The existing catalog fingerprint includes newly created protected objects, so
274 qualifies exact successor fingerprints and attestation receipts. That
fingerprint change gives the ingestor no new membership, grant or writer
function. Existing source and device writer specifications remain unchanged.
Deployment must re-attest the original ingestor ordinary TCP login on the same
live writer UID/generation after migration and retain one ESP32 connection.

The private PostgreSQL test fixture executes the real producer and reader,
ordinary API/MCP logins, the actual API route over HTTP, and the actual MCP
scorecard tool body through FastMCP registration and dispatch. It renders 2/96 (2.1%) qualified physical joint bins separately
from 85.8% attribution credit and 6.1% legacy binary readings. Its statements and
inputs are explicitly synthetic and never inserted into production. This
fixture proves the executable path, not physical commissioning or historical
authenticity. Full estate catalog/seal qualification is a separate rollback-only
rehearsal against the existing catalog, followed by actual live source identity
and login verification after deployment.

One initial emulated transaction-context ingestor attestation returned false
without a reproduced cause. The original capture is preserved alongside the
later exact repetitions. A rollback rehearsal with changed session identity is
not an actual TCP-login proof; delivery must verify the original live ingestor
connection and same writer UID/generation after the migration.
