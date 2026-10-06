# Iris reference inventory and natural-cycle qualification

Issue #953 corrects a source-to-runtime knowledge gap, not a provider/model change.
The model profile and upstream image pin remain unchanged. The `seed-config`
init container copies the source-owned Iris SOUL and two required references from
`verdify-iris-inventory` into the portable Hermes PVC on every start. Existing
learned `SKILL.md`, historical references, run state and credentials are retained.
On a new empty home only, the same bundle supplies a minimal bootstrap skill.

Edit `hermes/iris/SOUL.md`, `hermes/iris/references/full-plan-payload-preflight.md`
and `docs/planner/greenhouse-playbook.md`, then run:

```sh
python3 scripts/gen-iris-inventory-cm.py
make planner-dry
```

The generated ConfigMap and complete inventory hash in the Deployment are checked
by `tests/test_iris_inventory.py`. A hash change recreates the singleton Hermes
pod; no device connection or NFS change is part of this operation. Ingestor prompt
changes require its normal exact-SHA image promotion and Recreate rollout.
Coordinate the full Argo diff and sync with the ROOT delivery coordinator; do not
independently roll another component while a required cycle is in flight.

## Source and runtime qualification

Record the exact Git revision, ingestor build/digest, Hermes image/digest,
Deployment UID, pod UID/start time and both profile/inventory revision annotations.
Run this read-only inventory in the actual worker interpreter:

```sh
kubectl --context vallery -n verdify-prod exec -i deploy/verdify-hermes-iris \
  -c hermes-iris -- /opt/hermes/.venv/bin/python - \
  < scripts/verify-iris-inventory.py > iris-runtime-inventory.json
```

Compare its config SHA-256 against `hermes-config.yaml` data.config.yaml, SOUL and
both required reference hashes against the reviewed source bundle. The receipt
checks every declared learned-skill reference through the running worker's actual
`skill_view` implementation without shell preprocessing. It also fingerprints the
complete skill index. It prints hashes/status only, never learned content,
credentials or private plan context. A missing or unresolvable path fails the
inventory. Keep the pre-deploy receipt as evidence: the missing preflight is not
hidden by a successful historical plan.

## Actual plan acceptance

Capture the UTC rollout boundary and current `plan_delivery_log`/trigger ledger
IDs. Observe the next naturally scheduled SUNRISE, SUNSET, MIDNIGHT or
FORECAST_DEVIATION required-plan event after the new pod start; do not create a
synthetic trigger or send a provider request solely to validate this fix.

Join that event by exact `trigger_id` to its `plan_delivery_log.hermes_run_id`,
completed status/resulting_plan_id, `plan_journal` and persisted `setpoint_plan`
rows. Verify valid current coverage, expiry and schema-valid transitions. Record
IDs, timestamps and validation results without publishing raw private hypotheses
or assembled context. Use the corresponding bounded Hermes session/log window to
prove there were zero missing-skill/reference results or alias retries. Gateway
HTTP acceptance, process health and static readiness are separate observations;
none proves a plan was written. Retain pre-existing completed plan IDs.

Compare actual token/cost accounting to comparable earlier event types; investigate
any increase exceeding 20%. The prompt render adds approximately 200 characters,
not a new model or reasoning setting; a runtime skill read can still affect cost.
Do not claim unchanged cost from prompt size alone.

## Recovery

Capture previous managed SOUL and reference bytes privately, their hashes and the
current deployment revision before rollout. Revert the owned source/config/image
changes and synchronize the exact prior source if needed. The former version did
not seed SOUL/references, so reverting its Deployment alone cannot undo the new
PVC copies: restore those exact three managed files from the captured private
bundle before restarting. Preserve the learned skill and all unrelated PVC data.
Never delete the PVC or replace it with an empty one. Successful fallback is proven
by a real subsequent natural plan and unchanged exact-one-writer evidence.
