# Protocol-v2 shadow source lock

This directory freezes the Gate-2 inputs that are decisions of source, not
facts to infer from a running greenhouse. It is deliberately not a complete
`prepare_experiment_v2_shadow.py` input. `source-lock-v1.json` remains
`packet_ready=false` until the live evidence below is captured and checked.
Nothing here grants physical authority or contains a treatment mapping.

The selector message includes the source-locked meanings of all three profile
labels. This is load-bearing: the runtime request contains climate and forecast
context but does not otherwise send the profile artifact to the model. The
prompt therefore cannot be reduced to an unexplained list of three labels.

## Frozen source-owned inputs

- Study ID: `verdify-confirmed-component-switchback-v2-2026-08`.
- Experiment ID: `45039c86-c1d9-52f6-a0a9-d94a17bc4b14`, derived by the
  packet builder's stable UUIDv5 rule.
- Assignment/invocation namespace: `0c162b58-5a4c-5ddb-91fd-7d0ca68ff81f`,
  UUIDv5(URL, `https://verdify.com/identities/confirmed-component-switchback-v2/assignments`).
- Selector: Cortex `llm.primary.longctx`, medium reasoning, temperature zero,
  non-streaming, 512 output tokens, 60,000 ms per attempt, two attempts, no
  tools, one accepted choice per study/local day, baseline on every failure.
- Context cutoff: 23:45 America/Denver for the next 00:00 boundary. Scheduling
  must complete at least 12 hours before the boundary.
- Outcome duplicate tolerances: exactly `0.0` F and `0.0` kPa.
- Audit reference base: `gate2-shadow`. The command generator appends only its
  bounded action suffix; it does not derive authority from a source revision.

## Live evidence still required

Capture these values read-only; do not replace them with source assumptions.

1. **Deployed source, image, and config.** Read the three workload objects and
   their running pods in `verdify-prod`:

   ```text
   kubectl -n verdify-prod get deploy experiment-v2-lifecycle experiment-v2-selector experiment-v2-freezer -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.template.metadata.annotations.verdify\.io/config-revision}{"\t"}{.spec.template.spec.containers[0].image}{"\n"}{end}'
   kubectl -n verdify-prod get pods -l 'app.kubernetes.io/component in (experiment-v2-lifecycle,experiment-v2-selector,experiment-v2-freezer)' -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.annotations.verdify\.io/config-revision}{"\t"}{.spec.containers[0].image}{"\t"}{.status.containerStatuses[0].imageID}{"\n"}{end}'
   kubectl -n verdify-prod get deploy verdify-api verdify-mcp verdify-ingestor -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.template.metadata.annotations.verdify\.io/config-revision}{"\n"}{end}'
   ```

   Require all three pod-template `verdify.io/config-revision` values to equal
   `e7db51a9906e`; require their declared images and running `imageID` values to
   resolve to one registry digest. Require the OCI revision and the three
   containers' `VERDIFY_GIT_SHA` receipts to agree on one lower-case 40-hex
   deployed source, then inject that observed value as the preparation input's
   `source_git_sha`; the source lock deliberately contains no future deployed
   SHA because doing so would be self-referential. Use the 64 digest hex as
   `selector.runtime_environment_sha256`. Also require the API, MCP, ingestor,
   and all three orchestrator pod-template config revisions to equal the one
   generated config revision before recording it as the packet's live
   `candidate.config_revision`.

2. **Current firmware.** From the production read-only database role, capture:

   ```sql
   SELECT ts, firmware_version
   FROM public.diagnostics
   WHERE greenhouse_id = 'vallery'
     AND ts >= clock_timestamp() - interval '120 seconds'
     AND firmware_version IS NOT NULL
     AND firmware_version <> ''
   ORDER BY ts DESC
   LIMIT 1;
   ```

   Require exactly one row (the query's 120-second bound is the freshness
   contract) and bind its exact value as
   `candidate.firmware_revision`. The source candidate
   `2026.7.10.1500.09ee886` is not a substitute for this query.

3. **Running entity grid.** Capture the ingestor attestation produced from its
   one existing authenticated ESPHome entity/service enumeration and existing
   firmware state callback. Do not create another connection, enumeration,
   subscription or replay for this check. The attestor canonicalizes all 48
   registry fields in ascending wire-ID order with primary-device entity kind,
   object ID, minimum, maximum, step and typed route key (switches use null
   numeric bounds). Every source route and readback must match the enumerated
   object. The production revision must have the form
   `live-entity-grid-v1:sha256:<64 lower-case hex>`. Separately retain the
   prefix-replay evidence required by `verdify_schemas.component_executor`;
   entity enumeration alone does not qualify physical execution.

4. **Commissioning state.** From one complete current cfg-ingestion source
   epoch already produced by the sole authenticated subscription, require
   exactly one finite/on-grid value for each of the 48 canonical fields at one
   connection and writer generation. Do not open another subscription or make
   a device call. Normalize with
   `normalize_complete_state`, encode with `encode_policy_vector`, and write
   exactly:

   ```json
   {
     "schema": "verdify-experiment-v2-commissioning-state-v1",
     "wire_manifest_digest_hex": "0bdd80472f2a9845c24a78cd7d6662e0523314afc5f3149233c08a8e8aedb318",
     "wire_schema_version": 2,
     "wire_vector_hex": "<356 lower-case hex characters>"
   }
   ```

   Preserve the read-only enumeration/state receipt beside the operator audit;
   do not commit raw operational telemetry. This artifact supplies only the
   `commissioning_probe` registration state and grants no device action.

5. **Registry/image equality.** Verify the deployed ingestor image reports the
   exact source Git identity and therefore contains the registry file whose
   source hash is
   `5de3c7485b5f9d989602b9cbed29fbe7cc64e7a7f531bab7556148243b5d3b3b`.
   Only then bind
   `tunable-registry-v1:sha256:5de3c7485b5f9d989602b9cbed29fbe7cc64e7a7f531bab7556148243b5d3b3b`
   as `candidate.registry_revision`.

After all five checks agree, supply the live values to
`scripts/prepare_experiment_v2_shadow.py`. A generated packet is still
non-actuating: its manifest must retain every `no_authority_claims` value as
false and shadow admission remains closed.
