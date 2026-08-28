# Exact OpenAI direct-launch activation

This component is restricted to experiment
`45039c86-c1d9-52f6-a0a9-d94a17bc4b14` and issue #642. Its PreSync sequence
first proves one live `gpt-5.6-sol` Structured Outputs response, then consumes
the immutable attended physical-proof receipt to lock the 30-pair design. Only
after both hooks pass does Sync enable the API, lifecycle, selector, freezer,
and ingestor experiment consumers. `VERDIFY_POLICY_VECTOR_MODE` remains `off`.

The ingestor patch deliberately preserves the prior proof pod template exactly,
including its rollout annotation. Moving from the temporary proof component to
this component therefore does not recycle the sole device writer.

The API, ingestor, and three experiment orchestrators are pinned directly to
the qualified launch digests in this component. Routine global image-pin
updates therefore cannot change the experimental runtime or its hash-bound
analyzer environment during the 30-pair study.

Rollback is the ordinary GitOps reversal: remove this component from the prod
overlay and sync the reverting commit. The shared `verdify-config` remains
`off` with an empty active experiment ID, so removing these pod-local overrides
returns every consumer to coarse-off. Before declaring rollback complete,
verify the database admission is closed, there are no open experiment
exposures, all five workloads are Ready, and manually remove any now-orphaned
hook Jobs/ConfigMaps because the production Argo application uses
`prune:false`.
