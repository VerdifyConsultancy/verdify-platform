# Firmware digital-twin shadow — runbook (#34 / #255 / FW-OPT-6)

The merged-but-not-deployed twin (twin/Dockerfile + twin/offline_driver.py +
db/migrations/155-twin-observability-tables.sql + the
firmware-twin-divergence Grafana dashboard) stood up in k3s as a **read-only,
INSERT-only SHADOW**. Design: `docs/design/firmware-digital-twin.md` §2.1, §5.3,
§6.

> **2026-08-14 (#587, Lane F):** the component was reworked to the durable
> Tier-1 form — the pre-baked `verdify-twin` image (twin/Dockerfile, built
> in-cluster via `../twin-builder/twin-builder.yaml` into the zot origin)
> replaces the gcc initContainer + vendored `src/` ConfigMap + startup
> `pip install`, and the TCP-443 pip egress rule is removed (DB-only egress).
> The clone-and-compile steps below describe the RETIRED dev-proof shape and
> are kept as historical record. Deploying the durable form is §8.10 rollout
> step 4 (digest pin + overlays/prod wiring + migration/roles) — see
> `docs/runbooks/experiment-rollout.md`.

## Historical dev shadow proof (retired environment)

The `verdify-dev` namespace/overlay is now retired. The record below is kept as
historical proof of the shadow pipeline, not as a current deployment target.

1. `db/migrations/155-twin-observability-tables.sql` applied to the dev DB
   (additive: net-new `twin_decisions` + `firmware_twin_divergence` hypertables
   + `twin_ro` NOLOGIN role; idempotent; no ALTER of live data).
2. A `twin` LOGIN user grafted onto `twin_ro`; password sealed into
   `verdify-twin-secrets` (`TWIN_DB_PASSWORD` + `TWIN_DSN_BASE`), never in a
   manifest. The dev `greenhouses` table was seeded with a **device-key-free**
   `vallery` reference row (only id/name/timezone — NO esp32_api_key) to satisfy
   the `twin_decisions.greenhouse_id` FK.
3. `kubectl apply -k deploy/k8s/components/firmware-twin -n verdify-dev` — the
   shadow Deployment (gcc initContainer compiles `replay_emit_follow` from the
   src ConfigMap; python container runs `offline_driver.py` looping the corpus),
   its egress NetworkPolicy, and `allow-db-from-firmware-twin` (the namespace
   default-denies db ingress and the shared `allow-db-from-app` does not list
   `firmware-twin`).

**Proven:** pod 1/1 Running, 0 restarts; 400 `twin_decisions` rows written via
the INSERT-only role; L2 read-only verified at the DB
(`has_table_privilege('twin','equipment_state','INSERT') = f`,
`...'twin_decisions','INSERT' = t`); all 13 divergence-dashboard panel queries
resolve against the shadow data.

## NOT YET TRUSTWORTHY — the #31 gate (prod-vs-reality divergence)

`replay_emit_follow` itself warns that **48 `sp_*` Setpoints fields are absent
from the export corpus** and fall back to `default_setpoints()`. For any field
the dispatcher tuned away from default, the twin diverges for a CONFIG reason,
not a code reason — manufacturing **false prod-vs-reality alarm**. So this
shadow proves the *pipeline + dashboard render*; it does **NOT** license acting
on the divergence metric. **#31 (setpoint coverage) is the blocker** before the
live prod-vs-reality signal is a gate. The seeded `firmware_twin_divergence` row
is tagged `cmp_twin_ref='SEED-render-proof ...'` so it is never mistaken for a
real measurement.

> **2026-08-15 (#587, live adapter):** the live as-of feed is now BUILT —
> `db/migrations/211-twin-asof-input.sql` (security-barrier
> `v_policy_twin_asof_input` + append-only `twin_live_results` + twin-role
> grants) and `twin/live_driver.py` (incremental polling, 48-field vector
> decode onto the harness `sp_*` surface, §8.9 agreement/divergence/warm-up/
> unmatched-state/gap classification, boot-event twin-state reset). See the
> LIVE mode section below.

## LIVE mode (§8.9 live-shadow gate, #587)

`TWIN_MODE=live` on the Deployment switches the container from the corpus
loop to `twin/live_driver.py`. Prerequisites, in order:

1. Migration **211** applied (view + `twin_live_results` + grants — additive,
   idempotent, rollback-wrap validated against the prod schema).
2. The twin login user's group role holds migration 211's grants (both
   `twin_ro` and the experiment-era `verdify_twin_ro` name carry the same
   narrow surface: SELECT on `v_policy_twin_asof_input`, INSERT on
   `twin_live_results`, nothing else — the driver still runs the L2
   write-probe at startup and refuses to start otherwise).
3. Set `TWIN_MODE=live` via a component/overlay patch (GitOps only — never by
   editing the cluster). The NetworkPolicies are unchanged: DB-only egress,
   no actuation, no device route.

The §8.9 gate itself is computed by `twin/report_agreement.py` (operator/
analyst DSN — the twin role deliberately cannot read its own results):
byte-identical policy AND action agreement across a 7–14 day window with
per-day coverage/gap accounting, machine-readable JSON + canonical hash.
Warm-up, unmatched-state, and gap ticks never count as agreement.

Known §8.9 feed gaps (enumerated in the migration 211 header): no typed
firmware boot event (boots are inferred from the `diagnostics.uptime_s < 300`
drop), no per-relay last-off/runtime telemetry (the twin reconstructs dwell
state internally), no budget-remaining echo beyond `mister_water_today`, and
no resident-FSM echo (twin warm-up stands in). The ~26 wire fields whose
consumers sit outside the `greenhouse_logic.h` replay surface (mister
engagement, per-relay dwell fairness, enthalpy economizer, night VPD bias,
band pinch) are enumerated in `twin/live_driver.py::UNMAPPED_WIRE_FIELDS`;
they participate fully in policy-identity hash equality but not in the
harness action surface until the shared-oracle extraction (Lane E) lands.

## PROTECTED BY RUNTIME SAFEGUARDS — any future prod-DB shadow

Standing up the shadow in **verdify-prod** is a **live-prod schema change**
(migration 155 on the prod DB) + a prod `twin` login user. It is additive /
idempotent / non-destructive, but per the change-gating rule it needs an
explicit go + a DB snapshot first. When validated:

```
# 1. snapshot the prod DB (PITR/backup already runs; take a fresh logical dump).
# 2. apply migration 155 (additive, idempotent):
cat db/migrations/155-twin-observability-tables.sql \
  | kubectl exec -i -n verdify-prod verdify-db-0 -c postgres -- \
      psql -U verdify -d verdify -v ON_ERROR_STOP=1
# 3. create the prod twin login user + seal verdify-twin-secrets in verdify-prod
#    (prod greenhouses already has 'vallery' — no seed needed).
# 4. kubectl apply -k deploy/k8s/components/firmware-twin -n verdify-prod
# 5. wire the divergence dashboard into the grafana bucket CM + mount (same
#    pattern as band-tuning cm-2) — coordinate with the in-flight grafana PR to
#    avoid a duplicate-resource collision.
```

The twin remains read-only / INSERT-only / no-device-route in prod too (L1–L4).
The durable form replaces the gcc-initContainer with the pre-baked
`twin/Dockerfile` image, built in-cluster and digest-pinned from the zot
origin (`registry.vallery.net`; ghcr is banned per ADR-0021).
