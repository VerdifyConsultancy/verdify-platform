# Gravity Core Extraction

Use this reference when Gravity readiness depends on extracting reusable Gravity
core capabilities from Sunshine Club or another organization-specific
implementation.

`gravity-core-extraction` is a mode-first capability owned by
`gravity-readiness`. It produces a source-object inventory, reuse matrix,
generic-core and pack boundary, migration risk list, local filesystem evidence
ingestion pilot, readiness updates, and approval state. It does not authorize
Gravity feature implementation.

## Inputs

- Source repositories, refs, package paths, tests, migrations, Kubernetes
  resources, schemas, prompts, personas, vault rules, and business-story
  artifacts.
- Existing Gravity product, architecture, readiness, Onyx, platform, and
  Sunshine evidence.
- Source-object identity material: path, Git SHA, content hash, size, media
  type, provenance refs, and checksum strategy where available.
- Target boundaries for generic Gravity core, adapters, client packs, and
  discarded or deferred material.

## Procedure

1. Identify the source system, exact repository/ref, current boundary, and
   evidence refs.
2. Define the target core package and separate generic core responsibilities
   from client-pack responsibilities and non-goals.
3. Inventory source objects. For each object, record path, type, purpose,
   content identity, checksum strategy, provenance refs, reuse class, boundary
   decision, and notes.
4. Build a reuse matrix that maps source objects to `gravity_core`,
   `client_pack`, `adapter`, `docs`, `discard`, or `unknown`.
5. Record pack assumptions and leakage risks for organization-specific paths,
   names, environment variables, taxonomy, prompts, personas, schemas, vault
   rules, and business stories.
6. Name the contracts needed before implementation: schemas, APIs, MCP tools,
   file contracts, pack interfaces, readiness checks, and tests.
7. Record migration risks with severity, mitigation, owner, and blocking status.
8. Keep the first pilot scoped to local filesystem evidence ingestion and name
   entry criteria, exit criteria, human test steps, and evidence refs.
9. Record Gravity readiness checklist updates and validate the artifact against
   `../../schemas/gravity-core-extraction-plan.schema.yaml`.

## Completeness Rules

The artifact is incomplete when:

- source repository/ref or source-object identity is ambiguous;
- generic core and client-pack boundaries are mixed;
- pack-specific prompts, personas, schemas, taxonomy, paths, env vars, or
  business stories can leak into core without an explicit matrix decision;
- checksum strategy or provenance refs are missing for source objects;
- migration risks lack mitigation, owner, or blocking status;
- local filesystem ingestion pilot criteria are not named;
- approval state is absent or treated as implicit implementation permission.

## Stop Conditions

Stop and route to `gravity-readiness`, `platform-readiness`,
`architecture-contracts`, `state-of-union`, or human review when:

- extraction would require Gravity feature implementation before readiness
  approval;
- a source object cannot be classified as core, pack, adapter, docs, discard, or
  defer;
- pack assumptions are unresolved and could change generic core behavior;
- source-object identity or provenance cannot be collected;
- a blocking migration risk remains without owner and mitigation;
- the pilot would require platform, credentials, namespace, storage, or review
  evidence that has not passed readiness.
