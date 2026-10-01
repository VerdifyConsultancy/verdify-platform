# Existing-target authenticated ordinary clients

This adapter renders local Job and NetworkPolicy JSON only. It executes no
cluster operation, provisions no password, and changes no target database.
ROOT must first finish the actual role-complete import, reviewed native target
admission, and independently qualified catalog/ledger/seal custody. No fixture
or copied production receipt counts as admission.

## Target-only credentials

Agents central KSOPS owns three new namespace `verdify-db-rehearsal` Secrets:

| Secret | Key | Restored ordinary login |
|---|---|---|
| `verdify-cnpg-rehearsal-api-client-auth` | `password` | `verdify_api_runtime_login` |
| `verdify-cnpg-rehearsal-ingestor-client-auth` | `password` | `verdify_ingestor_runtime_login` |
| `verdify-cnpg-rehearsal-mcp-client-auth` | `password` | `verdify_mcp_runtime_login` |

Use new independent target-only passwords. Do not read/reuse production
credentials or alter the imported login names, flags, memberships, grantors,
ACLs, validity, or historical receipts. ROOT owns password-only provisioning
against the exact isolated primary; values stay in protected memory/SecretRefs,
never command arguments, source, plaintext files, DSN output, or exception logs.
Capture password-free role metadata parity and genuine native qualification
before/after. Password installation and actual authentication are separate facts.
The client bundle contains no Secret or owner credential.

## Render and review

Use current native JSON objects as one binding file with exact `cluster`, `pod`,
and `service` keys. The service is the existing Cluster RW Service. Preserve raw
binding SHA256, Cluster/primary Pod/Service UIDs, actual operand imageID, primary
address and source/target profile receipt custody. The target must be the
existing isolated Cluster with PostgreSQL16.13 and `verdify_rehearsal`.

For each consumer, ROOT supplies its actually qualified origin image digest,
full build source SHA, and SHA256 of the exact consumer module at that source.
The local worktree's consumer bytes must match that module hash. No candidate
image is inferred from current main. `VERDIFY_GIT_SHA` is read from the image,
never overridden in the rendered Job. API additionally supports its actual
`/etc/verdify/source-revision` fallback; a conflicting valid ENV/file pair fails.
Ingestor and MCP require their actual baked ENV revision. ROOT observed all
three existing managed7bb images carry the exact baked7bb ENV, with API also
carrying the same fallback file.

Logical source paths differ from image paths: `api/main.py` is `/app/main.py`,
`ingestor/ingestor.py` is `/app/ingestor/ingestor.py`, and `mcp/server.py` is
`/app/mcp/server.py`. Packaging tests bind these to actual Dockerfile COPYs.

```sh
python scripts/cnpg-runtime-client-qualification.py \
  --binding "$NATIVE_BINDING" --binding-sha256 "$RAW_BINDING_SHA256" \
  --consumer api --image "$QUALIFIED_API_IMAGE" --source "$API_BUILD_SOURCE" \
  --module-sha256 "$API_MODULE_SHA256" --profile-sha256 "$REVIEWED_TARGET_PROFILE_SHA256" \
  --suffix "$UNIQUE_SUFFIX" --output "$NEW_LOCAL_BUNDLE"
```

Repeat for ingestor/MCP using their actual image/source/module hashes. Output
is exclusively local, created without overwriting prior files. Review the two
narrow network policies and one Job. Clients have distinct qualification labels,
no API token, no ingress, and only DNS plus same-namespace target database5432
egress. Their labels do not acquire the database pods' Garage/API egress policy.
Database ingress is additive only from these same-namespace clients on5432.
ROOT owns actual adoption and one execution of each reviewed Job; no retry or
replacement Job is fabricated by this adapter.

## What actually runs

Each actual consumer image imports its module without starting API lifespan,
MCP serving, or ingestor main/device/MQTT/HA loops. API uses its actual pool init
and checkout hooks; ingestor uses its actual ordinary-role startup attestation;
MCP uses its actual bounded KPI pool factory/init. Connections use fresh scoped
passwords and the RW Service. No `SET SESSION AUTHORIZATION` or owner connection
substitutes for authentication.

Sessions start with `default_transaction_read_only=on`, the required search
path, and source-native login attestation. The probe checks session/current user,
database, server version, cluster name, primary status, actual backend IP and
read-only settings before executing the exact source-extracted hot SELECT in a
read-only transaction. Two separate pool checkouts repeat every identity and
read-only check after release/reset and API setup hooks. A second-checkout reset
to writable fails, even if the first hot query succeeded. Python runs isolated
(`-I`), preventing an inherited optimization flag from disabling assertions. API reads its band query, ingestor its climate timestamp,
and MCP its full climate query. Outputs contain identity/counts and custody
hashes, never rows, passwords, DSNs or exception text. A wrong password, identity,
backend, replica, source or attestation fails the Job.

## Required actual custody and evidence limits

ROOT rereads Cluster/Pod/Service UIDs, operand/consumer imageIDs and current
primary before and after each Job. Match backend address to that primary and
retain raw target profile/native receipt/readback facts. The caller-supplied
profile hash is a binding label; it does not independently prove installation.
Native consumer attestation checks the real qualified target receipts, while
ROOT's retained before/after profile facts bind that exact profile hash.

Retain each actual Job/Pod UID, terminal exit, source/image match, private logs,
raw binding and password-free role/catalog/ledger/seal readbacks. A successful
Job proves real password-authenticated consumer pool startup and hot SQL for
that exact isolated target. It does not run whole product services, move a
product endpoint, connect a controller, test write delivery, or prove PITR.
Unit driver doubles qualify refusal/cleanup/privacy behavior only; they are not
live authentication or restored-target credit. No live target has been qualified
by preparing this source.
