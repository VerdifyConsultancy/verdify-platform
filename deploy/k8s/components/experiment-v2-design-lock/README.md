# Dormant design-lock stage

This component is intentionally absent from every overlay. It is the first of
three separate GitOps stages for #588/#642 and must not be selected until the
fresh #641 proof receipt and exact runtime identities exist.

Before selection, generate two canonical ConfigMaps from the exact qualified
image/source revision:

- `verdify-experiment-v2-launch-design`, key `design.json`, validated by
  `experiment_orchestrator.launch_artifacts`;
- `verdify-experiment-v2-selector-identity`, key `identity.json`, validated by
  `SelectorIdentity.parse`.

The PreSync preflight has only OpenAI HTTPS authority. It persists a
metadata-only termination receipt containing provider/model/identity/request/
response hashes, but not the selected profile, credential, response body,
mapping, or randomization material. The next hook calls only the authenticated
component lifecycle API to consume the already sealed proof and lock the
canonical design.

Sync then enables only the lifecycle scheduler. Migration 238 permits its
internal OS-CSPRNG finalization exactly once, but returns
`awaiting_separate_day1_approval`. The selector, freezer, API experiment
consumer, and bounded ingestor executor remain coarse-off, admission remains
closed, and generalized vector mode remains off. Replace this stage first with
the authorization-only component. Only after its #642 receipt is retained may
a later Git change select the separate randomized-day1 activation.
